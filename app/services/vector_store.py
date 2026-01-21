import os
import sys
# Add the project root to the python path so imports work
sys.path.append(os.path.join(os.path.dirname(__file__), '..')) 

from dotenv import load_dotenv
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_openai import OpenAIEmbeddings
from supabase import create_client, Client

load_dotenv()

# Initialize the Supabase Client
supabase_url = os.environ.get("SUPABASE_URL")   
supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase = create_client(supabase_url, supabase_key)

# Initialize OpenAI Embeddings (The model that turns text into math)
embeddings = OpenAIEmbeddings()

def upload_documents_to_supabase(chunks: list[str]) -> SupabaseVectorStore:
    """
    Takes a list of text chunks, generates embeddings, 
    and inserts them into the 'documents' table in Supabase.
    """
    print(f"Uploading {len(chunks)} chunks to Supabase...")

    vector_store = SupabaseVectorStore.from_documents(
        documents=chunks,
        embedding=embeddings,
        client=supabase,
        table_name="whdocuments",
        query_name="match_whdocuments" # This matches the SQL function we made
    )
    
    print("Upload complete!")
    return vector_store


if __name__ == "__main__":
    from scripts.ingest import ingest_document

    file_path = "E:\\Code-repositories\\industrial-rag-backend\\data\\FS_Warehouse-control-system_EN.pdf"  # Replace with your PDF file path
    chunks = ingest_document(file_path)
    upload_documents_to_supabase(chunks)
