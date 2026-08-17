"""MilvusClient adapter for version-scoped hybrid RAG retrieval."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.evidence import EvidenceChunk, RetrievalFilters

KNOWLEDGE_CHUNKS_COLLECTION = "knowledge_chunks"


class MilvusChunkRepository:
    """Owns the physical hybrid-search schema and no application business logic."""

    def __init__(self, *, uri: str, collection_name: str = KNOWLEDGE_CHUNKS_COLLECTION, dimension: int = 1536):
        self.uri = uri
        self.collection_name = collection_name
        self.dimension = dimension

    def replace_document_version(self, records: Sequence[dict]) -> None:
        """Atomically replace an indexed document version's current chunks.

        The relational database remains the source of truth for chunks. Milvus
        only holds vectors and the metadata required to filter and cite them.
        """
        if not records:
            return
        version_ids = {item["document_version_id"] for item in records}
        if len(version_ids) != 1:
            raise ValueError("Each indexing operation must contain exactly one document version")
        client = self._client()
        self._ensure_collection(client)
        version_id = next(iter(version_ids))
        client.delete(
            collection_name=self.collection_name,
            filter=f'document_version_id == "{self._escape_filter_value(version_id)}"',
        )
        client.insert(collection_name=self.collection_name, data=list(records))

    def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        filters: RetrievalFilters,
        limit: int,
    ) -> list[EvidenceChunk]:
        """Fuse BM25 and dense results with RRF inside a selected review scope."""
        from pymilvus import AnnSearchRequest, RRFRanker

        client = self._client()
        self._ensure_collection(client)
        expression = self._filter_expression(filters)
        candidate_limit = max(limit * 3, 15)
        dense_request = AnnSearchRequest(
            data=[query_vector],
            anns_field="dense_vector",
            param={"metric_type": "COSINE"},
            limit=candidate_limit,
            expr=expression,
        )
        sparse_request = AnnSearchRequest(
            data=[query_text],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=candidate_limit,
            expr=expression,
        )
        results = client.hybrid_search(
            collection_name=self.collection_name,
            reqs=[dense_request, sparse_request],
            ranker=RRFRanker(),
            limit=limit,
            output_fields=[
                "document_version_id",
                "document_title",
                "document_type",
                "version",
                "page",
                "section",
                "content",
            ],
        )
        evidence: list[EvidenceChunk] = []
        for hits in results:
            for hit in hits:
                entity = hit["entity"]
                evidence.append(
                    EvidenceChunk(
                        chunk_id=str(hit["id"]),
                        document_version_id=entity["document_version_id"],
                        document_title=entity["document_title"],
                        document_type=entity["document_type"],
                        version=entity["version"],
                        page=entity.get("page"),
                        section=entity.get("section") or None,
                        content=entity["content"],
                        fused_score=float(hit["distance"]),
                    )
                )
        return evidence

    def _client(self):
        from pymilvus import MilvusClient

        return MilvusClient(uri=self.uri)

    def _ensure_collection(self, client) -> None:
        if client.has_collection(self.collection_name):
            return
        from pymilvus import DataType, Function, FunctionType

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=36)
        schema.add_field("document_version_id", DataType.VARCHAR, max_length=36)
        schema.add_field("document_title", DataType.VARCHAR, max_length=512)
        schema.add_field("document_type", DataType.VARCHAR, max_length=40)
        schema.add_field("version", DataType.VARCHAR, max_length=80)
        schema.add_field("system", DataType.VARCHAR, max_length=150)
        schema.add_field("page", DataType.INT64)
        schema.add_field("section", DataType.VARCHAR, max_length=512)
        schema.add_field(
            "content",
            DataType.VARCHAR,
            max_length=16384,
            enable_analyzer=True,
            analyzer_params={"type": "standard"},
        )
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=self.dimension)
        schema.add_function(
            Function(
                name="content_bm25",
                input_field_names=["content"],
                output_field_names=["sparse_vector"],
                function_type=FunctionType.BM25,
            )
        )
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
        index_params.add_index(
            field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25"
        )
        client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )

    def _filter_expression(self, filters: RetrievalFilters) -> str:
        ids = ", ".join(f'"{self._escape_filter_value(value)}"' for value in filters.document_version_ids)
        clauses = [f"document_version_id in [{ids}]"]
        if filters.system:
            clauses.append(f'system == "{self._escape_filter_value(filters.system)}"')
        if filters.document_types:
            types = ", ".join(f'"{self._escape_filter_value(value)}"' for value in filters.document_types)
            clauses.append(f"document_type in [{types}]")
        return " and ".join(clauses)

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
