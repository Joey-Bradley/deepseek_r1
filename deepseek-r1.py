import os
from openai import OpenAI, APIError, AutheticationError

client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com")

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
    ],
    #stream=False,
    temperature= 0.7,
    max_tokens=100,
    #reasoning_effort="high",
    #extra_body={"thinking": {"type": "enabled"}}
)

print(response.choices[0].message.content)