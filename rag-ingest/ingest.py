"""Create a RAG corpus in Vertex AI and import books from a GCS bucket."""
import os
import sys
import time

import vertexai
from vertexai import rag


PROJECT_ID = os.getenv("GCP_PROJECT_ID", "ai-architect-test-506013")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
BUCKET_NAME = os.getenv("GCS_BUCKET", "ai-architect-test-506013-rag-books")
CORPUS_DISPLAY_NAME = os.getenv("RAG_CORPUS_NAME", "books-corpus")
EMBEDDING_MODEL = "publishers/google/models/text-embedding-005"

CHUNK_SIZE = 512
CHUNK_OVERLAP = 100


def get_or_create_corpus():
    for corpus in rag.list_corpora():
        if corpus.display_name == CORPUS_DISPLAY_NAME:
            print(f"Reusing corpus: {corpus.name}")
            return corpus

    print(f"Creating corpus '{CORPUS_DISPLAY_NAME}'...")
    embedding_config = rag.RagEmbeddingModelConfig(
        vertex_prediction_endpoint=rag.VertexPredictionEndpoint(
            publisher_model=EMBEDDING_MODEL
        )
    )
    corpus = rag.create_corpus(
        display_name=CORPUS_DISPLAY_NAME,
        backend_config=rag.RagVectorDbConfig(
            rag_embedding_model_config=embedding_config,
        ),
    )
    print(f"Created corpus: {corpus.name}")
    return corpus


def import_books(corpus_name, gcs_paths):
    print(f"Importing from: {gcs_paths}")
    response = rag.import_files(
        corpus_name,
        gcs_paths,
        transformation_config=rag.TransformationConfig(
            chunking_config=rag.ChunkingConfig(
                chunk_size=CHUNK_SIZE,
                chunk_overlap=CHUNK_OVERLAP,
            ),
        ),
        max_embedding_requests_per_min=1000,
    )
    print(f"Imported: {response.imported_rag_files_count}")
    if response.failed_rag_files_count:
        print(f"Failed: {response.failed_rag_files_count}")


def list_files(corpus_name):
    files = list(rag.list_files(corpus_name))
    print(f"\nFiles in corpus ({len(files)}):")
    for f in files:
        print(f"  - {f.display_name}")


def main():
    vertexai.init(project=PROJECT_ID, location=LOCATION)

    corpus = get_or_create_corpus()
    import_books(corpus.name, [f"gs://{BUCKET_NAME}/"])

    time.sleep(3)
    list_files(corpus.name)

    print(f"\nCorpus name: {corpus.name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
