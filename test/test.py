from nimbic import NimbicClient
client = NimbicClient(
    api_key="nim_6ozm_3jnPIfApPcs3aY8w5C2PTIaKPIHlqSb5", 
    base_url="http://localhost:8000/v1" # Pointing to your local docker container
)
# This will route through your local container gateway!
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello Nimbic!"}]
)
print(response.choices[0].message.content)