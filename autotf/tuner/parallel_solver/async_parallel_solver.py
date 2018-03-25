import os
import time
import json
import logging
import numpy as np

from tuner.initial_design.init_random_uniform import init_random_uniform
from tuner.parallel_solver.base_parallel_solver import BaseParallelSolver
from concurrent.futures import ProcessPoolExecutor
from tuner.parallel_solver.base_parallel_solver import evaluate_func

logger = logging.getLogger(__name__)


class AsyncParallelSolver(BaseParallelSolver):

    def __init__(self, objective_func, lower, upper,
                 acquisition_func, model, maximize_func,
                 initial_design=init_random_uniform,
                 initial_points=3,
                 output_path=None,
                 train_interval=1,
                 n_restarts=1,
                 n_workers=4,
                 rng=None):
        """
        function description.

        Parameters
        ----------
        acquisition_func: BaseAcquisitionFunctionObject
            The acquisition function which will be maximized.
        """

        if rng is None:
            self.rng = np.random.RandomState(np.random.randint(100000))
        else:
            self.rng = rng

        self.model = model
        self.acquisition_func = acquisition_func
        self.maximize_func = maximize_func
        self.start_time = time.time()
        self.initial_design = initial_design
        self.objective_func = objective_func
        self.X = None
        self.y = None
        self.time_func_evals = []
        self.time_overhead = []
        self.train_interval = train_interval
        self.lower = lower
        self.upper = upper
        self.output_path = output_path
        self.time_start = None

        self.incumbents = []
        self.incumbents_values = []
        self.n_restarts = n_restarts
        self.init_points = initial_points
        self.runtime = []
        self.num_workers = n_workers
        self.pool = ProcessPoolExecutor(max_workers=n_workers)
        self.trial_statistics = []

    def run(self, num_iterations=10, X=None, y=None):
        """
        The main parallel optimization loop

        Parameters
        ----------
        num_iterations: int
            The number of iterations
        X: np.ndarray(N,D)
            Initial points that are already evaluated
        y: np.ndarray(N,1)
            Function values of the already evaluated points

        Returns
        -------
        np.ndarray(1,D)
            Incumbent
        np.ndarray(1,1)
            (Estimated) function value of the incumbent
        """
        # Save the time where we start the parallel optimization procedure
        self.time_start = time.time()

        self.trial_statistics = []

        if X is None and y is None:
            # Initial design
            X = []
            y = []

            start_time_overhead = time.time()
            init = self.initial_design(self.lower,
                                       self.upper,
                                       self.init_points,
                                       rng=self.rng)
            time_overhead = (time.time() - start_time_overhead) / self.init_points

            # Run all init config
            for i, x in enumerate(init):
                logger.info("Evaluate: %s", x)
                self.trial_statistics.append(self.pool.submit(evaluate_func, (self.objective_func, x)))

            # Wait all initial trials finish
            all_completed = False
            while not all_completed:
                all_completed = True
                for trial in self.trial_statistics:
                    if not trial.done():
                        all_completed = False
                        time.sleep(0.1)
                        break

            for i, trial in enumerate(self.trial_statistics):
                new_y, time_taken, _ = trial.result()
                X.append(init[i])
                y.append(new_y)
                self.time_func_evals.append(time_taken)
                self.time_overhead.append(time_overhead)
                logger.info("Configuration achieved a performance of %f in %f seconds",
                            new_y, time_taken)

                # Use best point seen so far as incumbent
                best_idx = np.argmin(y)
                incumbent = X[best_idx]
                incumbent_value = y[best_idx]

                self.incumbents.append(incumbent.tolist())
                self.incumbents_values.append(incumbent_value)

                self.runtime.append(time.time() - self.start_time)

                if self.output_path is not None:
                    self.save_output(i)

            self.X = np.array(X)
            self.y = np.array(y)
        else:
            self.X = X
            self.y = y

        # Main asynchronous parallel optimization loop

        self.trial_statistics = []
        evaluate_counter = self.init_points
        while evaluate_counter < num_iterations:
            if len(self.trial_statistics) > 2*self.num_workers:
                time.sleep(0.1)
            else:
                if (evaluate_counter+1) % self.train_interval == 0:
                    do_optimize = True
                else:
                    do_optimize = False

                # Choose next point to evaluate
                start_time = time.time()

                new_x = self.choose_next(self.X, self.y, do_optimize)
                self.time_overhead.append(time.time() - start_time)
                logger.info("Optimization overhead was %f seconds", self.time_overhead[-1])
                logger.info("Next candidate %s", str(new_x))

                self.trial_statistics.append(self.pool.submit(evaluate_func, (self.objective_func, new_x)))

                evaluate_counter += 1

            # Get the evaluation statistics
            self.collect()

        # Wait for all tasks finish
        if not len(self.trial_statistics):
            all_completed = False
            while not all_completed:
                all_completed = True
                for trial in self.trial_statistics:
                    if not trial.done():
                        all_completed = False
                        time.sleep(0.1)
                        break
            self.collect()

        logger.info("Return %s as incumbent with error %f ",
                    self.incumbents[-1], self.incumbents_values[-1])

        return self.incumbents[-1], self.incumbents_values[-1]

    def collect(self):
        # Get the evaluation statistics
        for trial in self.trial_statistics:
            if trial.done():
                new_y, time_taken, new_x = trial.result()
                self.time_func_evals.append(time_taken)
                logger.info("Configuration achieved a performance of %f ", new_y)
                logger.info("Evaluation of this configuration took %f seconds", self.time_func_evals[-1])

                # Extend the data
                self.X = np.append(self.X, new_x[None, :], axis=0)
                self.y = np.append(self.y, new_y)

                # Estimate incumbent
                best_idx = np.argmin(self.y)
                incumbent = self.X[best_idx]
                incumbent_value = self.y[best_idx]

                self.incumbents.append(incumbent.tolist())
                self.incumbents_values.append(incumbent_value)
                logger.info("Current incumbent %s with estimated performance %f",
                            str(incumbent), incumbent_value)

                self.runtime.append(time.time() - self.start_time)

                if self.output_path is not None:
                    self.save_output(evaluate_counter)

                self.trial_statistics.remove(trial)


    def choose_next(self, X=None, y=None, do_optimize=True):
        """
        Suggests a new point to evaluate.

        Parameters
        ----------
        X: np.ndarray(N,D)
            Initial points that are already evaluated
        y: np.ndarray(N,1)
            Function values of the already evaluated points
        do_optimize: bool
            If true the hyperparameters of the model are
            optimized before the acquisition function is
            maximized.
        Returns
        -------
        np.ndarray(1,D)
            Suggested point
        """

        if X is None and y is None:
            x = self.initial_design(self.lower, self.upper, 1, rng=self.rng)[0, :]

        elif X.shape[0] == 1:
            # We need at least 2 data points to train a GP
            x = self.initial_design(self.lower, self.upper, 1, rng=self.rng)[0, :]

        else:
            try:
                logger.info("Train model...")
                t = time.time()
                self.model.train(X, y, do_optimize=do_optimize)
                logger.info("Time to train the model: %f", (time.time() - t))
            except:
                logger.error("Model could not be trained!")
                raise
            self.acquisition_func.update(self.model)

            logger.info("Maximize acquisition function...")
            t = time.time()
            x = self.maximize_func.maximize()

            logger.info("Time to maximize the acquisition function: %f", (time.time() - t))

        return x

    def save_output(self, it):

        data = dict()
        data["optimization_overhead"] = self.time_overhead[it]
        data["runtime"] = self.runtime[it]
        data["incumbent"] = self.incumbents[it]
        data["incumbents_value"] = self.incumbents_values[it]
        data["time_func_eval"] = self.time_func_evals[it]
        data["iteration"] = it

        json.dump(data, open(os.path.join(self.output_path, "tuner_iter_%d.json" % it), "w"))
