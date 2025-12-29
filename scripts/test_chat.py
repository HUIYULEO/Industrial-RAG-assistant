import sys
import os

# Fix path to allow importing from 'app'
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.services.chat_engine import get_chat_response

if __name__ == "__main__":
    # Ask a question specifically about the PDF you uploaded
    # Example: "What is the safety protocol for the AGV?" 
    user_query = input("Ask a question:\n> ")
    
    answer = get_chat_response(user_query)
    
    print("\n--- AI ANSWER ---")
    print(answer)
    print("-----------------")