import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from supabase import create_client

from pydantic import Field
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun

load_dotenv()

# Initialize the Supabase Client
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase = create_client(supabase_url, supabase_key)

# Initialize OpenAI Embeddings and Chat Model
embeddings = OpenAIEmbeddings()
llm = ChatOpenAI(model_name="gpt-4o", temperature=0)

class SupabaseRPCRetriever(BaseRetriever):
    client: Any = Field(description="Supabase client")
    embeddings: Any = Field(description="Embeddings model")
    query_name: str = Field(description="RPC function name")
    k: int = Field(default=3, description="Number of documents to retrieve")
    content_field: str = Field(default="content", description="Field name for content")
    match_threshold: float = Field(default=0.0, description="Similarity threshold")

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
        **kwargs: Any,
    ) -> List[Document]:
        query_vec = self.embeddings.embed_query(query)

        params: Dict[str, Any] = {
            "query_embedding": query_vec,
            "match_threshold": self.match_threshold,
            "match_count": self.k,
        }

        res = self.client.rpc(self.query_name, params).execute()
        rows = res.data or []

        print(f"Retrieved {len(rows)} relevant documents from Supabase.")

        # 添加更多元数据，方便调试和追踪
        documents = []
        for row in rows:
            metadata = {
                "id": row.get("id"),
                "similarity": row.get("similarity"),  # 如果 RPC 返回 similarity
            }
            # 添加其他你需要的字段
            if "metadata" in row:
                metadata.update(row["metadata"])
            
            documents.append(
                Document(
                    page_content=row.get(self.content_field) or "",
                    metadata=metadata
                )
            )
        return documents


def get_chat_response(question: str) -> str:
    print(f"thinking about: {question}...")

    retriever = SupabaseRPCRetriever(
        client=supabase,
        embeddings=embeddings,
        query_name="match_whdocuments",
        k=3,
        content_field="content",  # if your RPC returns 'page_content', change to "page_content"
    )

    prompt = ChatPromptTemplate.from_template(
        """You are a helpful assistant.
        Use the following context to answer the question.
        If the answer cannot be found in the context, say you don't know.

        Context:
        {context}

        Question: {input}
        Answer:"""
    )

    document_chain = create_stuff_documents_chain(llm, prompt)
    rag_chain = create_retrieval_chain(retriever, document_chain)

    response = rag_chain.invoke({"input": question})

    # print("response:", response)
    return response["answer"]
