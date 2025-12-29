import os
import sys

# Add the project root to the python path so imports work
#sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from app.services.vector_store import upload_documents_to_supabase

def ingest_document(file_path):
    loader = PyPDFLoader(file_path)
    documents = loader.load()
    
    # This is the "DSA" part: How do we slice the data effectively?
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150
    )
    chunks = text_splitter.split_documents(documents)
    print(f"Split {len(documents)} pages into {len(chunks)} chunks.")

    #for chunk in chunks: print(chunk.page_content)  # Print the first chunk as a sample

    upload_documents_to_supabase(chunks)
    return chunks

if __name__ == "__main__":
    file_path = "E:\Code-repositories\industrial-rag-backend\data\What is WCS_ [Educational Guide to Warehouse Control Systems].pdf"  # Replace with your PDF file path
    ingest_document(file_path)