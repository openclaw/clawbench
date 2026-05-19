import json
import argparse
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def compute_horvitz_thompson_estimator(results_path, weights_path):
    """
    Computes the Horvitz-Thompson (or Hajek) estimator for the mean performance.
    Let X_i be the performance on task i from stratum k_i.
    The unbiased estimator for E_P[X] is 1/N \sum_i rho_{k_i} X_i.
    """
    with open(results_path, 'r') as f:
        results = json.load(f)
        
    with open(weights_path, 'r') as f:
        weights = json.load(f)

    # To ensure consistency and finite sample robustness, we normalize weights (Hajek estimator)
    # sum_rho = \sum_i rho_{k_i}
    
    weighted_sum = 0.0
    sum_weights = 0.0
    
    n = len(results)
    if n == 0:
        logging.info("Empty sample. Estimator undefined.")
        return
        
    for task_id, data in results.items():
        stratum = data.get("topic")
        score = data.get("score", 0.0)
        
        rho = weights.get(stratum, 1.0)
        
        weighted_sum += rho * score
        sum_weights += rho
        
    if sum_weights == 0:
        logging.error("Sum of importance weights is zero. Target measure P may be singular w.r.t Q.")
        return

    # Asymptotically efficient Hajek estimator
    theta_hat = weighted_sum / sum_weights
    unadjusted_mean = sum(d.get("score", 0) for d in results.values()) / n
    
    logging.info(f"Sample Size (n) = {n}")
    logging.info(f"Unadjusted Empirical Mean (Q-measure) = {unadjusted_mean:.4f}")
    logging.info(f"Adjusted Posterior Mean (P-measure, Hajek Estimator) = {theta_hat:.4f}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate unbiased posterior scoring via IPW.")
    parser.add_argument("--results", required=True, help="Path to raw execution results (JSON)")
    parser.add_argument("--weights", required=True, help="Path to computed weights (JSON)")
    args = parser.parse_args()
    
    compute_horvitz_thompson_estimator(args.results, args.weights)
