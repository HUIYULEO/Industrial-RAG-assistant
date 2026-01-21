import os
import sys
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from supabase import create_client  
from langchain_community.vectorstores import SupabaseVectorStore
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

# Initialize the Supabase Client
supabase_url = os.environ.get("SUPABASE_URL")   
supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase = create_client(supabase_url, supabase_key)

# Initialize OpenAI Embeddings and Chat Model
embeddings = OpenAIEmbeddings()

# connect to the existing Supabase vector store, just pointing to the right table
vector_store = SupabaseVectorStore(
    embedding=embeddings,
    client=supabase,
    table_name="whdocuments",
    query_name="match_whdocuments"
)   

# set up the LLM and the Retrieval QA chain
llm = ChatOpenAI( model_name="gpt-4o", temperature=0 )

def get_chat_response(question: str) -> str:
    """
    Takes a user question, finds relevant manual pages, 
    and generates an answer.
    """
    print(f"thinking about: {question}...")

    retriever = vector_store.as_retriever(search_kwargs={"k": 3})
    
    prompt = ChatPromptTemplate.from_template(
        """You are a helpful assistant.
        Use the following context to answer the question.
        If the answer cannot be found in the context, say you don't know.

        Context:
        {context}

        Question: {input}
        Answer:"""
    )
    #create the chain
    document_chain = create_stuff_documents_chain(llm, prompt)
    rag_chain = create_retrieval_chain(retriever, document_chain)

    # Run the chain
    response = rag_chain.invoke({"input": question})

    return response["answer"]

