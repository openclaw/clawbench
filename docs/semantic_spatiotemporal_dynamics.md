# Semantic Spatio-Temporal Dynamics Analysis

## 1. Introduction: Bridging Space and Time

Evaluating iterative, long-running Large Language Model (LLM) agents requires understanding two fundamentally different axes of their behavior:
1. **The Semantic Space (What the agent is doing)**: The distribution of tasks, intents, and prompts the agent interacts with.
2. **The Temporal Dynamics (How the agent evolves)**: The trajectory of the agent over time, characterized by its ability to converge on solutions versus drifting into unrecoverable hallucination loops.

Historically, evaluating these dimensions in isolation creates a blind spot. **Raw temporal dynamics metrics treat all tasks in an arbitrary benchmark equally.** If a benchmark dataset over-represents simple, tightly constrained tasks, the agent's overall dynamic stability will look artificially robust. Conversely, if it over-indexes on open-ended creative tasks, the agent might look chaotic.

The **Semantic Spatio-Temporal Dynamics** framework solves this by fusing these two methodologies. It maps the geometry of the agent's time-series trajectories directly onto a debiased, user-aligned semantic manifold, projecting abstract mathematical stability metrics onto concrete operational realities.

---

## 2. The Spatial Dimension: Task Distribution Reweighting

Evaluation datasets ($Q$) inherently suffer from distribution shifts compared to true real-world usage ($P$). To correct this, we stratify and reweight the semantic space of tasks.

### 2.1 NLU/NLI Semantic Clustering
We embed the natural language instructions of each task $q_i$ using Dense NLU models to capture semantic intent, and employ Natural Language Inference (NLI) to confirm entailment and redundancy. 
Using clustering algorithms (e.g., HDBSCAN), we partition the dataset into $K$ distinct functional stratums: $\mathcal{C} = \{C_1, C_2, \dots, C_K\}$.

### 2.2 Importance Weighting (Radon-Nikodym Derivatives)
Let $Q(C_k)$ be the empirical fraction of the evaluation dataset belonging to cluster $C_k$, and $P(C_k)$ be the target real-world probability of that cluster. We compute the importance weight (Radon-Nikodym derivative) for any task $i$ in stratum $k_i$ as:
$$ \rho_{k_i} = \frac{P(C_{k_i})}{Q(C_{k_i})} $$
This scaling factor ensures that over-represented tasks are suppressed, and under-represented but critical real-world tasks are amplified.

---

## 3. The Temporal Dimension: Long-Term Trajectory Dynamics

As an agent iteratively reasons and invokes tools, its transcript generates a sequence of discrete actions $x_t$. We project this sequence into a continuous $d$-dimensional behavioral feature space to analyze its geometry.

### 3.1 Attractor Geometry and The Constraint Index $C(q)$
For a given task $q$, we measure how tightly the agent's trajectory is bound to an attractor basin using three core metrics:
*   **Participation Ratio (PR) & Rényi Dimension ($D_2$)**: We extract the eigenspectrum of the trajectory's covariance matrix. The Rényi correlation dimension $D_2 = -\log_2 \sum p_i^2$ measures the structural volume/complexity of the phase space explored by the agent.
*   **Response Entropy ($H$)**: The Shannon entropy over the eigenspectrum (or discrete action distribution) measuring the intrinsic uncertainty and diffusion of the agent.
*   **Bayesian Optimal Prediction Score (BOPS)**: A measure of inter-run predictability, proxying how consistently the agent targets the maximum a posteriori (MAP) trajectory.

These are standardized and fused into the **Constraint Index $C(q)$**, where a high $C(q)$ implies tight bounded behavior (a strong point attractor).

### 3.2 Perturbation Sensitivity (Lyapunov Proxy)
To test robustness, we generate semantically identical but lexically perturbed prompts $q'$. We track the divergence between the original trajectory $e_t$ and the perturbed trajectory $e'_t$ over time, extracting a Lyapunov-like proxy:
$$ \widehat{\lambda}(t) = \frac{1}{t}\log\frac{D_t+\epsilon}{D_0+\epsilon} $$
A positive $\widehat{\lambda}(t)$ indicates chaotic sensitivity, where tiny prompt variations cause exponentially diverging behavior.

### 3.3 Dynamical Regimes
Trajectories are ultimately classified into distinct kinetic states:
*   **Trapped**: Collapsing into a highly recurrent, localized subset of actions.
*   **Limit Cycle**: Bounded drift with quasi-periodic revisits to states.
*   **Wandering/Diffusive**: Unbounded expansion with low predictability and high entropy.

---

## 4. Spatio-Temporal Fusion: The Hajek Estimator

The core theoretical leap is applying the Spatial weights ($\rho_i$) to the Temporal properties ($D_i$) to estimate the *true expected real-world dynamics*.

For any dynamic property $D$, the debiased expectation under the real-world user distribution $P$ is given by the asymptotically efficient Hajek estimator:
$$ \mathbb{E}_{P}[D] \approx \frac{\sum_{i=1}^N \rho_{k_i} D_i}{\sum_{i=1}^N \rho_{k_i}} $$

### Key Fused Metrics
1.  **Expected Regime Probability ($E_P[\text{Regime} = r]$)**: Instead of stating "20% of benchmark trajectories hit a chaotic wandering regime," this calculates the exact probability that a *deployed user* will experience that failure mode.
2.  **Debiased Survival Curves ($S_{debiased}(t)$)**: A weighted Kaplan-Meier estimation. If simple, high-survival tasks are overrepresented in the benchmark, the raw curve is falsely optimistic. The debiased curve corrects this, providing a true expected time-to-failure.
3.  **Expected Chaos ($E_P[\widehat{\lambda}]$) & Predictability ($E_P[C(q)]$)**: The true weighted average of prompt fragility and system volatility.

---

## 5. Expanding the Spatial Definition: State, Action, and Conditioned Survival

While the standard formulation defines "Space" via the NLU embedding of the *initial prompt*, this framework is naturally extensible to other spatial dimensions of the trajectory:

*   **Action Space (Tools Called)**: Stratifying trajectories based on the specific tools invoked (e.g., isolating all runs where `edit_file` or `bash` was called).
*   **Intermediate State Space**: Stratifying based on the environment state or agent memory (e.g., isolating runs where a `SyntaxError` was encountered).

This is where **Time-to-Event (Survival Analysis)** breaks back in with immense power. Because ClawBench logs the full trajectory state, we can compute dynamically conditioned expected properties. Rather than just asking "What is the expected survival time of this task?", we can condition on any arbitrary combination of parameters:
*   $\mathbb{E}[\text{Time-to-Failure} \mid \text{Tool} = \text{bash}]$
*   $\mathbb{E}[\text{Probability of Limit Cycle} \mid \text{State} = \text{SyntaxError}]$

By using Stratified Kaplan-Meier curves or Cox Proportional Hazards models with time-dependent covariates, researchers can isolate the exact state-action transitions that induce catastrophic drift.

---

## 6. Interpretation and Impact for Researchers

Merging these dimensions unlocks powerful theoretical and practical insights:

*   **Kaplan-Yorke and Hidden Fragility**: If the Spatio-Temporal fusion reveals a high expected Rényi dimension $D_2$ and high Lyapunov sensitivity $\widehat{\lambda}$, the deployed agent lacks a definitive "point attractor" for real-world tasks. An agent might appear stable on a benchmark, but if its chaotic trajectories align heavily with the most frequent user tasks, its operational stability is critically low.
*   **Ergodicity and Markovian Traps**: LLMs are generally non-ergodic due to absorbing states (completing a task or hitting turn limits). However, when trapped in a limit cycle, they suffer from context blindness, collapsing into a destructive Markovian loop. The Spatio-Temporal framework identifies exactly *which semantic regions* trigger these non-ergodic traps, allowing researchers to surgically apply early-stopping heuristics rather than blanket constraints.
*   **Task-Sensitivity Mutual Information $I(q; \lambda)$**: There is massive mutual information between a task's Constraint Index $C(q)$ and its perturbation sensitivity. Tightly constrained tasks yield deep attractor basins with near-zero sensitivity. The Spatio-Temporal framework proves mathematically where *prompt engineering matters most*—specifically on the loosely constrained tasks that dominate a user's target distribution.

---

## 7. Implementation Pipeline

The Spatio-Temporal decomposition is fully operationalized through a bridging script that ingests the outputs of both upstream modules:

1.  **Spatial Baseline**: `scripts/compute_posterior_weights.py` computes the weights $\rho_i$ based on NLU clusters and user schemas.
2.  **Temporal Baseline**: `scripts/run_posterior_dynamics_pipeline.py` computes the unweighted survival, regimes, and constraint indices.
3.  **Spatio-Temporal Fusion**: `scripts/compute_debiased_dynamics.py` applies the Hajek estimators to produce the final `debiased_regimes_probability` and `debiased_expected_C_q`.
