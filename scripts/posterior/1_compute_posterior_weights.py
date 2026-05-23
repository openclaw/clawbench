import json
import argparse
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def compute_radon_nikodym_derivatives(empirical_path, target_path, output_path):
    """
    Computes the importance weights (Radon-Nikodym derivatives) dP/dQ
    where P is the target user measure and Q is the empirical design measure.
    By Slutsky's theorem, plug-in estimators using these weights will yield
    asymptotically consistent estimators of the expected performance under P.
    """
    with open(empirical_path, 'r') as f:
        q_dist = json.load(f)  # Q: empirical measure

    with open(target_path, 'r') as f:
        p_dist = json.load(f)  # P: target measure

    weights = {}
    for stratum in p_dist:
        # Let q_k = Q(stratum), p_k = P(stratum).
        # Weight rho_k = p_k / q_k
        q_k = q_dist.get(stratum, 0.0)
        p_k = p_dist.get(stratum, 0.0)

        if q_k == 0:
            if p_k > 0:
                logging.warning(f"Strata '{stratum}' has P-measure > 0 but Q-measure = 0. Estimator lacks support!")
            weights[stratum] = 0.0
        else:
            weights[stratum] = p_k / q_k

    with open(output_path, 'w') as f:
        json.dump(weights, f, indent=4)
    logging.info(f"Computed Radon-Nikodym derivatives (weights) saved to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute importance weights for posterior scoring.")
    parser.add_argument("--empirical", required=True, help="Path to empirical measure Q (JSON)")
    parser.add_argument("--target", required=True, help="Path to target measure P (JSON)")
    parser.add_argument("--output", required=True, help="Path to output weights (JSON)")
    args = parser.parse_args()

    compute_radon_nikodym_derivatives(args.empirical, args.target, args.output)
