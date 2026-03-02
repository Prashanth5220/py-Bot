import asyncio
from rasa.core.agent import Agent
import os

async def verify():
    # Load the latest model
    model_path = "models"
    models = [f for f in os.listdir(model_path) if f.endswith(".tar.gz")]
    if not models:
        print("No model found!")
        return
    
    latest_model = sorted(models)[-1]
    print(f"Loading model: {latest_model}")
    
    agent = Agent.load(os.path.join(model_path, latest_model))
    
    test_phrase = "book an appointment for cardiology ex.dr.prshanth"
    print(f"\nTesting phrase: '{test_phrase}'")
    
    result = await agent.parse_message(test_phrase)
    
    print("\nResult:")
    print(f"Intent: {result['intent']['name']} (Confidence: {result['intent']['confidence']:.2f})")
    print("Entities:")
    for entity in result['entities']:
        print(f" - {entity['entity']}: {entity['value']}")

if __name__ == "__main__":
    asyncio.run(verify())
