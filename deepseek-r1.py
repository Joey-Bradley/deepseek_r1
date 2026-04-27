import os
from dotenv import load_dotenv
from openai import OpenAI, APIError, AuthenticationError

# Force load from that specific path
load_dotenv()

# 2. Grab the key
api_key = os.getenv("DEEPSEEK_API_KEY")

# 3. Check if it actually loaded before starting the client
#if not api_key:
    #raise ValueError("CRITICAL: DEEPSEEK_API_KEY not found! Check your .env file name and location.")

# 4. Initialize
client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com"
)


# Initialize the client with DeepSeek's specific base URL

stream = client.chat.completions.create(
    model="deepseek-reasoner",
    messages=[
        {"role": "user", "content": "Explain quantum computing in simple terms?"},
        #{"role": "user", "content": "Hello"},
    ],
    
    temperature= 0.7,
    max_tokens=1000,
    stream=True,
    #reasoning_effort="high",
    #extra_body={"thinking": {"type": "enabled"}}
)

if stream:
  print("Response")
  for chunk in stream:
    if chunk.choices[0].delta.content:
      print(chunk.choices[0].delta.content,
            end="", flush=True)