"""Word2Vec (skip-gram com negative sampling) implementado em numpy puro.

O gensim não tem wheel pré-compilada para Python 3.14 no Windows neste
ambiente (a extensão `word2vec_inner` precisa compilar C, e exige o
Microsoft C++ Build Tools, que não está instalado) — em vez de depender
disso, esta é uma implementação direta do algoritmo, suficiente para o
corpus pequeno deste trabalho (253 documentos curtos).

Referência do algoritmo: Mikolov et al., 2013 ("Distributed Representations
of Words and Phrases and their Compositionality").
"""

from __future__ import annotations

import numpy as np


class Word2VecSimples:
    def __init__(
        self,
        vector_size: int = 100,
        window: int = 4,
        negative: int = 5,
        epochs: int = 100,
        learning_rate: float = 0.025,
        min_count: int = 1,
        seed: int = 42,
    ) -> None:
        self.vector_size = vector_size
        self.window = window
        self.negative = negative
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.min_count = min_count
        self.rng = np.random.default_rng(seed)

        self.word2idx: dict[str, int] = {}
        self.idx2word: list[str] = []
        self.W_in: np.ndarray | None = None   # vetores de "palavra alvo"
        self.W_out: np.ndarray | None = None  # vetores de "palavra contexto"
        self._unigram_dist: np.ndarray | None = None  # pra amostragem negativa

    def _construir_vocabulario(self, sentencas: list[list[str]]) -> None:
        contagem: dict[str, int] = {}
        for sent in sentencas:
            for w in sent:
                contagem[w] = contagem.get(w, 0) + 1
        vocab = [w for w, c in contagem.items() if c >= self.min_count]
        self.idx2word = vocab
        self.word2idx = {w: i for i, w in enumerate(vocab)}

        freqs = np.array([contagem[w] for w in vocab], dtype=np.float64)
        # Distribuição unigrama elevada a 3/4 (truque padrão do paper original
        # pra suavizar a amostragem negativa em favor de palavras raras).
        dist = freqs ** 0.75
        self._unigram_dist = dist / dist.sum()

    def treinar(self, sentencas: list[list[str]]) -> "Word2VecSimples":
        self._construir_vocabulario(sentencas)
        V = len(self.idx2word)
        if V == 0:
            raise ValueError("vocabulário vazio — corpus sem tokens válidos")

        limit = 0.5 / self.vector_size
        self.W_in = self.rng.uniform(-limit, limit, size=(V, self.vector_size))
        self.W_out = np.zeros((V, self.vector_size))

        pares: list[tuple[int, int]] = []
        for sent in sentencas:
            idxs = [self.word2idx[w] for w in sent if w in self.word2idx]
            for pos, centro in enumerate(idxs):
                ini = max(0, pos - self.window)
                fim = min(len(idxs), pos + self.window + 1)
                for ctx_pos in range(ini, fim):
                    if ctx_pos == pos:
                        continue
                    pares.append((centro, idxs[ctx_pos]))

        if not pares:
            return self

        pares_arr = np.array(pares)
        for epoch in range(self.epochs):
            self.rng.shuffle(pares_arr)
            for centro_idx, contexto_idx in pares_arr:
                self._passo_sgns(int(centro_idx), int(contexto_idx))

        return self

    def _passo_sgns(self, centro_idx: int, contexto_idx: int) -> None:
        """Um passo de gradiente de skip-gram com negative sampling, pro par
        (palavra central, palavra de contexto real) + `negative` palavras
        negativas amostradas da distribuição unigrama."""
        v_centro = self.W_in[centro_idx]

        negativos = self.rng.choice(len(self.idx2word), size=self.negative, p=self._unigram_dist)
        alvos = np.concatenate(([contexto_idx], negativos))
        rotulos = np.concatenate(([1.0], np.zeros(self.negative)))

        v_alvos = self.W_out[alvos]
        scores = v_alvos @ v_centro
        preds = 1.0 / (1.0 + np.exp(-np.clip(scores, -20, 20)))
        erro = preds - rotulos

        grad_out = np.outer(erro, v_centro)
        grad_centro = erro @ v_alvos

        self.W_out[alvos] -= self.learning_rate * grad_out
        self.W_in[centro_idx] -= self.learning_rate * grad_centro

    def vetor_documento(self, tokens: list[str]) -> np.ndarray:
        """Média dos vetores das palavras do documento que estão no
        vocabulário (abordagem padrão pra transformar word embeddings em
        representação de documento)."""
        idxs = [self.word2idx[w] for w in tokens if w in self.word2idx]
        if not idxs:
            return np.zeros(self.vector_size)
        return self.W_in[idxs].mean(axis=0)
