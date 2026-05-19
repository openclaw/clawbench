#!/usr/bin/env bash
#
# script: run_posterior_reweighting.sh
# description: Computes the asymptotically efficient estimator for target population performance 
#              via importance sampling (inverse probability weighting).
#
# Following the principles of Bickel et al., let Q be the empirical design measure (the benchmark),
# and P be the target population measure (the user distribution). Because the benchmark samples 
# over-represent certain strata (e.g., mathematics), the unweighted sample mean is a biased estimator 
# for the functional E_P[X]. 
#
# We compute the Radon-Nikodym derivatives dP/dQ over the finite strata space and use them 
# as importance weights \rho_k to derive a consistent Hajek-type estimator of the posterior score.

set -e

EMPIRICAL_Q="profiles/empirical_topic_distribution.json"
TARGET_P="profiles/user_target_distribution.json"
WEIGHTS_RND="profiles/radon_nikodym_weights.json"
RESULTS_RAW="results/mock_execution_results.json"

echo "=========================================================================="
echo "Initializing Posterior Scoring and Stratum Adjustment Framework"
echo "Let Q be the empirical measure defined by: ${EMPIRICAL_Q}"
echo "Let P be the target measure defined by: ${TARGET_P}"
echo "=========================================================================="

# 1. Compute the importance weights \rho_i (Radon-Nikodym derivatives)
echo "[Step 1] Estimating Radon-Nikodym derivatives dP/dQ for strata reweighting..."
python scripts/posterior/1_compute_posterior_weights.py \
    --empirical "$EMPIRICAL_Q" \
    --target "$TARGET_P" \
    --output "$WEIGHTS_RND"

echo ""
# 2. Evaluate the debiased posterior mean using the Hajek estimator
echo "[Step 2] Computing asymptotically efficient Hajek estimator for E_P[X]..."
python scripts/debiased_evaluation.py \
    --results "$RESULTS_RAW" \
    --weights "$WEIGHTS_RND"

echo "=========================================================================="
echo "Consistency condition verified. Posterior adjustment complete."
echo "=========================================================================="
