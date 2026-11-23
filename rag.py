"""Simple keyword RAG over the markdown files in knowledge/.

To upgrade later: replace the TF-IDF vectors in build_index()/search() with an
embedding model, and store the chunk vectors in a vector database. Everything else
(load_chunks, the search() return shape) can stay the same."""
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
_index = None


def load_chunks() -> list[dict]:
    """Split every markdown file on '## ' headings. Each chunk keeps its source file."""
    chunks = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        parts = ("\n" + path.read_text()).split("\n## ")
        intro, sections = parts[0].strip(), parts[1:]
        if intro:
            chunks.append({"source": path.name, "heading": "", "text": intro})
        for section in sections:
            heading = section.split("\n", 1)[0].strip()
            chunks.append({"source": path.name, "heading": heading, "text": "## " + section.strip()})
    return chunks


def build_index() -> dict:
    global _index
    chunks = load_chunks()
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    matrix = vectorizer.fit_transform([c["text"] for c in chunks])
    _index = {"chunks": chunks, "vectorizer": vectorizer, "matrix": matrix}
    return _index


def search(query: str, k: int = 4) -> list[dict]:
    """Return up to k chunks as {source, text, relevance}, dropping zero-relevance hits."""
    index = _index or build_index()
    scores = cosine_similarity(index["vectorizer"].transform([query]), index["matrix"])[0]
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [{"source": index["chunks"][i]["source"], "text": index["chunks"][i]["text"],
             "relevance": round(float(scores[i]), 3)} for i in ranked if scores[i] > 0]
