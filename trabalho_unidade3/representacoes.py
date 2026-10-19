"""Constrói as quatro representações textuais (BoW, TF-IDF, Word2Vec, BERT)
pro corpus de ocorrências, e expõe uma função de busca unificada que
funciona em cima de qualquer uma delas.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from trabalho_unidade3.corpus import Documento, tokenizar
from trabalho_unidade3.word2vec_simples import Word2VecSimples

NOMES_REPRESENTACOES = ["bow", "tfidf", "word2vec", "bert"]


@dataclass
class Representacao:
    nome: str
    matriz_docs: np.ndarray  # (n_docs, dim) — já normalizada por linha quando faz sentido
    vetorizar_consulta: callable  # str -> np.ndarray (dim,)


def _normalizar_linhas(m: np.ndarray) -> np.ndarray:
    normas = np.linalg.norm(m, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return m / normas


def construir_bow(textos: list[str]) -> Representacao:
    vectorizer = CountVectorizer(tokenizer=tokenizar, lowercase=False, token_pattern=None)
    matriz = vectorizer.fit_transform(textos).toarray().astype(np.float64)

    def vetorizar(consulta: str) -> np.ndarray:
        return vectorizer.transform([consulta]).toarray().astype(np.float64)[0]

    return Representacao("bow", matriz, vetorizar)


def construir_tfidf(textos: list[str]) -> Representacao:
    vectorizer = TfidfVectorizer(tokenizer=tokenizar, lowercase=False, token_pattern=None)
    matriz = vectorizer.fit_transform(textos).toarray().astype(np.float64)

    def vetorizar(consulta: str) -> np.ndarray:
        return vectorizer.transform([consulta]).toarray().astype(np.float64)[0]

    return Representacao("tfidf", matriz, vetorizar)


def construir_word2vec(textos: list[str], **kwargs) -> tuple[Representacao, Word2VecSimples]:
    sentencas = [tokenizar(t) for t in textos]
    modelo = Word2VecSimples(**kwargs).treinar(sentencas)
    matriz = np.array([modelo.vetor_documento(s) for s in sentencas])
    matriz = _normalizar_linhas(matriz)

    def vetorizar(consulta: str) -> np.ndarray:
        v = modelo.vetor_documento(tokenizar(consulta))
        norma = np.linalg.norm(v)
        return v / norma if norma > 0 else v

    return Representacao("word2vec", matriz, vetorizar), modelo


def construir_bert(textos: list[str], modelo_nome: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") -> Representacao:
    from sentence_transformers import SentenceTransformer

    modelo = SentenceTransformer(modelo_nome)
    matriz = modelo.encode(textos, normalize_embeddings=True, show_progress_bar=False)

    def vetorizar(consulta: str) -> np.ndarray:
        return modelo.encode([consulta], normalize_embeddings=True, show_progress_bar=False)[0]

    return Representacao("bert", np.array(matriz), vetorizar)


def buscar(rep: Representacao, consulta: str, docs: list[Documento], top_k: int = 5) -> list[dict]:
    """Devolve os `top_k` documentos mais similares à consulta (cosseno),
    ordenados por similaridade decrescente."""
    vetor_consulta = rep.vetorizar_consulta(consulta).reshape(1, -1)
    sims = cosine_similarity(vetor_consulta, rep.matriz_docs)[0]
    ordem = np.argsort(-sims)[:top_k]
    return [
        {
            "rank": i + 1,
            "score": float(sims[idx]),
            "id": docs[idx].id,
            "texto": docs[idx].texto,
            "periodico": docs[idx].periodico,
            "ano": docs[idx].ano,
            "event_type": docs[idx].event_type,
        }
        for i, idx in enumerate(ordem)
    ]
