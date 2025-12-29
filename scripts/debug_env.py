import sys
import os

# Fix path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from supabase import create_client

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
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
    client: Any
    embeddings: Any
    query_name: str
    k: int = 3
    content_field: str = "content"  # change if needed

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
            "match_threshold": 0.0,
            "match_count": self.k,
        }

        res = self.client.rpc(self.query_name, params).execute()
        rows = res.data or []

        return [Document(page_content=(row.get(self.content_field) or "")) for row in rows]

def debug_search(query):
    print(f"\n--- DEBUGGING QUERY: '{query}' ---")

    retriever = SupabaseRPCRetriever(
        client=supabase,
        embeddings=embeddings,
        query_name="match_whdocuments",
        k=3,
        content_field="content",  # if your RPC returns 'page_content', change to "page_content"
    )
    
    # Perform a similarity search directly
    # k=5 means "Give me the top 5 matches"
    docs_with_scores = retriever.similarity_search_with_score(query, k=5)
    
    if not docs_with_scores:
        print("❌ NO RESULTS FOUND in Database.")
        print("Possible causes:")
        print("1. Table 'documents' is empty.")
        print("2. 'match_documents' function in SQL is missing or broken.")
        return

    print(f"✅ Found {len(docs_with_scores)} matches:\n")
    
    for i, (doc, score) in enumerate(docs_with_scores):
        print(f"[Match {i+1}] Similarity Score: {score:.4f}")
        print(f"Content Preview: {doc.page_content[:150]}...")
        print("-" * 40)

if __name__ == "__main__":
    test_query = input("Enter your question: ")
    debug_search(test_query)