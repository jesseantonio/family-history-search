"""Agrupamento de menções de pessoa a partir do score par-a-par (Fase 5).

Duas abordagens, pra comparar como o README pede ("1. regras e limiar de
similaridade; 2. clustering hierárquico"): (1) regra e limiar simples, via
componentes conexos; (2) clustering hierárquico (scipy), cortado na mesma
distância-limiar, pra dar uma comparação direta e justa entre os dois.

DBSCAN/HDBSCAN e comunidades em grafo (itens 3 e 4 do README) não foram
implementados aqui — sem uma amostra revisada manualmente (Fase 0) pra
calcular B-cubed ou taxa de acerto, comparar mais métodos não teria como
ser validado, só multiplicaria opções sem critério pra escolher entre
elas.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from src.resolution.features import PersonMentionRecord, ScoreBreakdown, compute_score

DEFAULT_THRESHOLD = 0.55


@dataclass(frozen=True)
class PairwiseScore:
    i: int
    j: int
    breakdown: ScoreBreakdown


def compute_all_pairs(mentions: list[PersonMentionRecord]) -> list[PairwiseScore]:
    return [
        PairwiseScore(i=i, j=j, breakdown=compute_score(mentions[i], mentions[j]))
        for i in range(len(mentions))
        for j in range(i + 1, len(mentions))
    ]


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def cluster_by_threshold(n: int, pairs: list[PairwiseScore], threshold: float = DEFAULT_THRESHOLD) -> list[list[int]]:
    uf = _UnionFind(n)
    for pair in pairs:
        if pair.breakdown.total >= threshold:
            uf.union(pair.i, pair.j)
    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(uf.find(idx), []).append(idx)
    return list(groups.values())


def cluster_hierarchical(n: int, pairs: list[PairwiseScore], threshold: float = DEFAULT_THRESHOLD) -> list[list[int]]:
    if n < 2:
        return [[i] for i in range(n)]
    dist = np.ones((n, n))
    np.fill_diagonal(dist, 0.0)
    for pair in pairs:
        d = 1.0 - pair.breakdown.total
        dist[pair.i, pair.j] = d
        dist[pair.j, pair.i] = d
    condensed = squareform(dist, checks=False)
    z = linkage(condensed, method="complete")
    labels = fcluster(z, t=1.0 - threshold, criterion="distance")
    groups: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        groups.setdefault(int(label), []).append(idx)
    return list(groups.values())
