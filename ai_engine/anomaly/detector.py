import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor


def scores(values):
    a = np.asarray(values, dtype=float).reshape(-1, 1)
    if len(a) < 5:
        return [0.0] * len(a)
    m = IsolationForest(random_state=42, contamination="auto").fit(a)
    raw = -m.decision_function(a)
    lo = raw.min(); hi = raw.max()
    return np.round((raw - lo) / (hi - lo + 1e-9), 4).tolist()


def multivariate_scores(matrix):
    X = np.asarray(matrix, dtype=float)
    if len(X) < 6:
        return [0.0] * len(X), [0.0] * len(X)
    iso = IsolationForest(random_state=42, contamination="auto").fit(X)
    iso_raw = -iso.decision_function(X)
    n_neighbors = max(2, min(10, len(X) - 1))
    # LOF warns on exact duplicate feature vectors. A tiny deterministic
    # perturbation only for duplicated rows keeps the case-baseline model
    # numerically stable without materially changing the feature scale.
    X_lof = X.copy()
    _, inverse, counts = np.unique(X_lof, axis=0, return_inverse=True, return_counts=True)
    duplicated_groups = {idx for idx, count in enumerate(counts) if count > 1}
    if duplicated_groups:
        for row_idx, group_idx in enumerate(inverse):
            if group_idx in duplicated_groups:
                scale = np.maximum(1.0, np.abs(X_lof[row_idx]))
                X_lof[row_idx] = X_lof[row_idx] + (1e-6 * (row_idx + 1)) * scale
    lof = LocalOutlierFactor(n_neighbors=n_neighbors, contamination="auto")
    lof.fit_predict(X_lof)
    lof_raw = -lof.negative_outlier_factor_

    def scale(v):
        v = np.asarray(v, dtype=float); return (v - v.min()) / (v.max() - v.min() + 1e-9)
    return np.round(scale(iso_raw), 4).tolist(), np.round(scale(lof_raw), 4).tolist()
