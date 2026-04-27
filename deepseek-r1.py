import os
from dotenv import load_dotenv
from openai import OpenAI, APIError, AuthenticationError

# Load variables from .env
load_dotenv()

# Access the key
api_key = os.getenv("OPENAI_API_KEY")

client = OpenAI()

response = client.chat.completions.create(
    model="deepseek-reasoner",
    messages=[
        {"role": "user", "content": "Explain quantum computing in simple terms"},
        #{"role": "user", "content": "Hello"},
    ],
    #stream=False,
    temperature= 0.7,
    max_tokens=1000,
    #reasoning_effort="high",
    #extra_body={"thinking": {"type": "enabled"}}
)

print(response.choices[0].message.content)