# When Large Language Models Are Dreaming, Where Do They Go?
## Investigating the Long-Term Dynamics of Long-Running LLM Reasoning Systems

Long-running LLM-based agents are increasingly used for autonomous planning and reasoning, yet their behavior is typically studied only over short horizons. When an LLM repeatedly conditions on its own outputs, it forms an iterative stochastic process whose long-term dynamics remain poorly characterized. This document outlines an empirical framework that treats LLM reasoning/agent loops as dynamical systems and studies their asymptotic behavior under varying degrees of prompt constraint.

---

## 1. Introduction: The Need for Dynamical Diagnostics

**Key question: what happens if we keep an LLM agent running?**

Large language models (LLMs) are increasingly deployed within long-running reasoning and agentic systems that iteratively plan, reflect, and revise in natural language. In these settings, a model repeatedly conditions on its own outputs, forming an iterative stochastic process whose behavior extends far beyond single-step inference. Despite extensive work on short-horizon accuracy and capability, we lack a principled understanding of the **long-term dynamics** of such systems: whether they converge to stable behaviors, enter cycles, drift semantically, or exhibit sensitivity to small perturbations when constraints weaken.

This gap is especially important for **reliability and safety**. Long-horizon instability may manifest as goal drift, runaway loops, incoherence, or brittle behavior under minor prompt changes. Conversely, stable attractor-like behavior may explain why some agentic systems remain controllable over long durations. We therefore treat long-running LLM reasoning not merely as next-token prediction, but as a **dynamical system evolving in semantic space**.

---

## 2. Methodology: Experiment & Formulation

### 2.1 System Definition (Rollouts)
Fix a model $M$, a loop template $\mathcal{T}$, sampling parameters $\theta$ (e.g., temperature/top-$p$), a horizon $H$, and a random seed $r$. Starting from a query $q$, generate a trajectory $\tau=(x_t)_{t=0}^{H}$ by repeated self-conditioning. Conceptually, this defines an observed stochastic dynamical system:

$$ x_{t+1} \sim \mathcal{K}_{M,\mathcal{T},\theta}(\,\cdot \mid x_t, q\,) $$

where $\mathcal{K}$ is the transition kernel induced by the model, template, and decoding.

### 2.2 Query Design and the Constraint Index $C(q)$
We construct a controlled prompt set spanning general-purpose vs. domain-specific, open-ended vs. closed objective, and self-referential vs. task-oriented instructions. For each query $q$, we compute a **Constraint Index** $C(q)$ using three measurable components:

1.  **Topic Coverage (Participation Ratio / PCA Dimension)**
    Embed an initial batch of responses to $q$ (or short rollouts), compute covariance $\Sigma_q$, and define effective dimension:
    $$ \mathrm{PR}(q) = \frac{\bigl(\mathrm{tr}(\Sigma_q)\bigr)^2}{\mathrm{tr}(\Sigma_q^2)} $$
2.  **Ambiguity / Diversity (Entropy Proxy)**
    We measure action-space diversity using **Shannon Entropy ($H$) over tool-family categorical distributions** across the transcript steps, acting as a proxy for the ambiguity of the prompt.
3.  **Repetition / Predictability (Bayesian Optimal Prediction Score - BOPS)**
    Quantify predictability via a BOPS computed from an optimal predictor over the observed history. Higher values indicate stronger repetitive structure.

We combine these components (e.g., z-scored weighted sum) into $C(q)$ and retain each component for ablations.

> **Implementation:** Computed in `scripts/posterior/2_compute_constraint_index.py` and powered by `clawbench.dynamics.compute_dynamics`.

### 2.3 State Representations (Behavioral Action-Space Embeddings)
At each step, we map text $x_t$ to a semantic state. Rather than relying on dense pre-trained textual NLU embeddings (which can dilute intent), we use a structured **10-dimensional Behavioral Feature Matrix**.
*   **Embedding space:** Extracted directly from the agent's actions, features include: `[0:6]` proportions of tool-family usage (e.g., `browser`, `execute`, `search`), `[6]` success/error flags, `[7]` normalized token consumption, `[8]` normalized text length, and `[9]` temporal trajectory progress.

We compute uncertainty (logit entropy/self-consistency), drift and step size ($\|e_t-e_1\|$, $\|e_t-e_{t-1}\|$), recurrence (kNN revisits), and distance to an early-step centroid.

> **Implementation:** Computed in `clawbench.dynamics.Dynamics` representations.

### 2.4 Effective Volume and Manifold-Aware Support
For a window $E=\{e_t\}_{t=1}^T$, we treat "volume" as a proxy for support size/coverage. With empirical covariance $\Sigma$:
$$ \mathrm{Vol}_{\log}(E) = \log\det(\Sigma + \varepsilon I) $$
We also estimate intrinsic dimension $\widehat{m}$ and a robust radius $r$ (median kNN distance), yielding $V_{\mathrm{eff}} \propto r^{\widehat{m}}$.

> **Implementation:** Computed via covariance matrices within `clawbench.dynamics.compute_dynamics`.

### 2.5 Clustering Tasks via PCA Participation Ratio
We use the Participation Ratio ($PR$) to mathematically cluster tasks based on the size of their dynamic attractors:
*   **High $PR$ Clusters (Diffusive/Wandering)**: Tasks with ambiguous instructions. The variance is distributed across many principal components, implying isotropic diffusion across a wide semantic space.
*   **Low $PR$ Clusters (Trapped/Convergent)**: Highly constrained tasks with clear checks. The variance is dominated by a few components, showing rapid collapse to a specific path or limit-cycle.
By calculating the distance between centroids of these clusters in PCA space, we determine if similar tasks converge to the same dynamical basin, and observe how perturbations shift trajectories within or across these clusters.

> **Implementation:** PR values are extracted via `clawbench.dynamics.compute_dynamics` and aggregated in `scripts/posterior/2_compute_constraint_index.py`.

---

## 3. Perturbation Sensitivity ($\widehat{\lambda}(t)$)

For each query $q$, we create perturbed variants $q'$ (lexical/syntactic paraphrases and controlled semantic nudges). We run matched rollouts and compare trajectories via $D_t=d(e_t,e'_t)$ and a Lyapunov-like divergence-rate proxy:

$$ \widehat{\lambda}(t) = \frac{1}{t}\log\frac{D_t+\epsilon}{D_0+\epsilon} $$

A positive $\widehat{\lambda}(t)$ indicates extreme sensitivity, where tiny changes in prompt conditions lead to exponentially diverging behavior sequences over the horizon, often resulting in regime switching.

> **Implementation:** Computed directly via `clawbench.dynamics.compute_sensitivity`.

---

## 4. Theory-Guided Signatures and Expected Regimes

We expect distinct empirical dynamical regimes across the landscape of tasks and models:
1.  **Trapped/Attractor-like:** low support size (small $\mathrm{Vol}_{\log}$), high recurrence, high predictability (high BOPS).
2.  **Limit-cycle-like:** high recurrence with bounded drift and quasi-periodic revisits.
3.  **Diffusive/Wandering:** increasing support size and drift with low recurrence.
4.  **High Sensitivity:** small perturbation $\delta(q,q')$ yields large long-horizon divergence (large $\widehat{\lambda}(t)$).

Empirically, weaker constraints (lower $C(q)$) increase long-run sensitivity and diffusion, while stronger constraints induce bounded behavior. The trajectory $S_t = \phi(x_t)$ induces an approximate time-homogeneous Markov kernel $P(S_t, \cdot)$, yielding testable hypotheses:

### Ergodicity and Convergence Rates
If $P$ is ergodic with stationary distribution $\pi$:
$$ \frac{1}{T}\sum_{t=1}^T f(S_t) \;\xrightarrow[T\to\infty]{}\; \mathbb{E}_{\pi}[f] $$
When a contraction-like bound holds (e.g., Dobrushin coefficient $<1$), windowed metrics rapidly stabilize. *Diagnostic:* Windowed averages flatten; shrinking seed-to-seed dispersion.

> **Implementation:** Bound approximations are verified via variance reductions in `clawbench.dynamics.StratifiedAssessment.reweight`.

### Mixing Diagnostics via Dependence Coefficients
Decay of dependence reveals mixing vs. periodicity:
$$ I(S_t;S_{t+k}) \;\to\; 0 \quad (k\to\infty) $$
*Diagnostic:* Autocorrelation curves and return-time plots.

> **Implementation:** Autocovariance logic forms the core of `clawbench.dynamics._classify_regime`.

### Information-Theoretic Structure & Guidance
The entropy rate limits predictability:
$$ h = \lim_{t\to\infty} H(S_{t+1}\mid S_{1:t}) \le H(S_{t+1}) $$
Innovation is separated from memory via $I(S_{t+1};S_{1:t})$. Lower decoding temperatures generally reduce entropy proxies but empirically we must verify if this yields "healthy stabilization" or collapses into repetitive traps.

> **Implementation:** Entropy calculation relies on `clawbench.dynamics.compute_dynamics` (`tool_entropy`).

### R\'enyi and Correlation Dimensions
For the correlation integral $C_T(r)$, the correlation dimension is:
$$ D_2 = \lim_{r\downarrow 0}\frac{d\log C_T(r)}{d\log r} $$
More generally, R\'enyi dimensions $D_q$ reveal attractor complexity. *Diagnostic:* Saturation of $PR$ and $D_q$ implies attraction to a low-dimensional set.

> **Implementation:** PCA eigenvalue saturation evaluated in `clawbench.dynamics.compute_dynamics`.

### Bayesian Optimal Prediction Score (BOPS)
The expected one-step log-loss equals conditional entropy:
$$ \inf_{\hat p_t}\;\mathbb{E}\bigl[-\log \hat p_t(S_{t+1})\bigr] = H(S_{t+1}\mid S_{1:t}) $$
Normalized into a predictive probability score (BOPS), it reveals when a process becomes algorithmically predictable. Furthermore, for each step, measuring the entropy of the next action predicted by the model alongside its argmax allows us to bound (via a Lagrangian relaxation) how much information is lost by taking the Bayesian optimal or greedy action.

> **Implementation:** Integrated into the $C(q)$ calculation within `scripts/posterior/2_compute_constraint_index.py`.

### Survival Analysis & Latent-State Markov Models
Treating failure (e.g., incoherence/runaway) as an absorbing event $T_F$, survival statistics quantify long-term resilience:
$$ \mathsf{S}(t) = \mathbb{P}(T_F > t), \qquad h(t) = \mathbb{P}(T_F = t \mid T_F \ge t) $$

> **Implementation:** Extracted and plotted via `clawbench.dynamics.kaplan_meier` and aggregated in `scripts/survival_analysis.py`.

### Queueing-Style Stability (Foster-Lyapunov Drift)
If the loop maintains a backlog $Q_t$ of unresolved subgoals:
$$ \mathbb{E}[V(Q_{t+1})-V(Q_t)\mid Q_t] \le b - \epsilon\,\mathbf{1}\{Q_t>0\} $$
Negative drift ensures stability, while positive drift mathematically aligns with runaway "hallucination" narratives.

> **Implementation:** Evaluated analytically as drift metrics in `clawbench.dynamics._classify_regime`.

---

## 5. Pipeline Implementation: Posterior Computation

The theoretical framework is operationalized through the `run_posterior_dynamics_pipeline.py` script. This pipeline sequentially calls several specialized analysis scripts on the cached execution traces to map the raw behavior onto the dynamical concepts:

*   **`scripts/posterior/2_compute_constraint_index.py`**: Computes the task-level Constraint Index $C(q)$. It calculates the PCA Participation Ratio ($PR$), tool-family entropy ($H$), and Bayesian Optimal Prediction Score (BOPS) to quantify how tightly the prompt constraints bind the model's exploration.
*   **`classify_regimes.py`**: Operationalizes the regime signatures. It classifies each individual run into one of the theoretical regimes (`trapped`, `convergent`, `diffusive`, `chaotic`, `limit_cycle`, or `unknown`) using thresholds on entropy, drift variance, and step-size autocovariance.
*   **`variance_decomp.py`**: Separates performance variance into *seed noise* versus actual *capability signal*. This quantifies the Signal-to-Noise Ratio (SNR) of the task, isolating the dynamical sensitivity to stochasticity from true deterministic performance.
*   **`survival_analysis.py`**: Implements the latent-state failure modeling. It computes Kaplan-Meier survival curves $S(t)$ and hazard functions $h(t)$, defining "failure" $T_F$ as an absorbing event (like a runaway loop or an unrecoverable `tool_misuse`), plotting model resilience over the turn horizon.
*   **`snr_weighted_ranking.py`**: Computes an alternative task-weighted ranking. Instead of a flat mean, it weights tasks based on their signal density: $w_q = \max(0, \text{SNR}(q)) \times |C(q)|$. This penalizes models specifically for failing on highly-constrained, low-noise tasks.
*   **`generate_dynamical_report.py`**: Handles **Visualization and Reporting**. It aggregates the mathematical diagnostics across all scripts into a comprehensive markdown summary report (`EVAL_REPORT_DYNAMICAL.md`). This renders comparative tables for Kaplan-Meier survival curves, SNR-weighted rankings, and regime distributions, setting up the visualizations needed to compare the geometry of the dynamical basins.

---

## 6. Interpretation and Impact

Framing long-running LLM agents as dynamical systems yields practical diagnostics for reliability. By triangulating results across embedding geometry, uncertainty signals, and survival curves, this framework exposes why some agentic architectures succeed while others wander off-task.

For LLM Agent Researchers and End-Users, these metrics translate directly to operational guarantees:

*   **Lyapunov Sensitivity and Attractor Dimensions (The Kaplan-Yorke connection)**: If an agent's behavioral dimension (Rényi $D_2$) and maximal Lyapunov proxy ($\widehat{\lambda}$) are high, the agent lacks a robust "point attractor" (a definitive solution). For researchers, this means the agent is exploring chaotically and is highly fragile to prompt wording. For users, it means the agent's behavior is fundamentally unpredictable and shouldn't be trusted for deterministic workflows.
*   **Ergodicity and Markovian Traps**: Because LLMs have absorbing states (e.g., max-turn limits, task completion), they are generally non-ergodic. However, when an agent falls into a "trapped" limit cycle (repeating a failed tool call), it suffers from context blindness, collapsing into a destructive Markovian state. For researchers, detecting non-ergodic trapping is the key to designing better early-stopping or self-reflection triggers.
*   **Task-Sensitivity Mutual Information $I(q; \lambda)$**: There is massive mutual information between the initial task's constraint index $C(q)$ and the resulting perturbation sensitivity $\widehat{\lambda}$. Tightly constrained tasks (high $C(q)$, e.g., "fix a specific syntax error") yield deep attractor basins with near-zero sensitivity. Open-ended tasks (low $C(q)$, e.g., "refactor this module") yield flat basins where tiny prompt changes cause exponential divergence. For users, this proves that *prompt engineering is most critical on loosely constrained tasks*, whereas highly constrained tasks are structurally robust to variations.


---

## 7. Space-Time Decomposition

Our raw time-series metrics treat all tasks in the benchmark equally. However, benchmarks rarely reflect true user workloads. To correct this, we integrate the temporal dynamics computed here with the spatial Task Distribution Reweighting framework.

By taking the Radon-Nikodym derivatives (Importance Weights $\rho_i$) representing the true user distribution, we compute the Hajek estimators for all dynamic properties. This **Space-Time Decomposition** yields the expected real-world probability of an agent entering a specific dynamical regime (like a chaotic wandering state) and the debiased expected Constraint Index $C(q)$ under operational conditions.

> **Implementation:** Computed by `scripts/compute_debiased_dynamics.py`, which fuses the NLU-based importance weights with the raw posterior dynamics artifacts generated by this pipeline.

---

## 8. Inspired By

The theoretical framework and diagnostics outlined in this document draw inspiration from the following works:

*   [Understanding Chain-of-Thought in LLMs through Information Theory](https://arxiv.org/html/2411.11984v2) (arXiv:2411.11984)
*   [Is Chain-of-Thought Reasoning of LLMs a Mirage? A Data Distribution Lens](https://arxiv.org/html/2508.01191v3) (arXiv:2508.01191)
*   [Uncovering Meanings of Embeddings via Partial Orthogonality](https://arxiv.org/abs/2310.17611) (arXiv:2310.17611)
*   [Skewed Memorization in Large Language Models: Quantification and Decomposition](https://arxiv.org/abs/2502.01187) (arXiv:2502.01187)
