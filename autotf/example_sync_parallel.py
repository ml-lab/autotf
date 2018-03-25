
import numpy as np

from tuner.fmin import bayesian_optimization


import logging
logging.basicConfig(level=logging.INFO)


# The optimization function that we want to optimize.
# It gets a numpy array with shape (1,D) where D is the number of input dimensions
def objective_function(x):
    y = np.sin(3 * x[0]) * 4 * (x[0] - 1) * (x[0] + 2)
    return y

# Defining the bounds and dimensions of the input space
lower = np.array([0])
upper = np.array([6])

# Start Bayesian optimization to optimize the objective function
results = bayesian_optimization(objective_function, lower, upper, num_iterations=50, n_workers=4)

print(results["x_opt"])
print(results["f_opt"])
