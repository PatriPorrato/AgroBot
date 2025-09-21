import os
import tweepy

# Levanta las claves desde las variables de entorno (que vos ya guardaste en GitHub)
client = tweepy.Client(
    consumer_key=os.getenv("X_API_KEY"),
    consumer_secret=os.getenv("X_API_SECRET"),
    access_token=os.getenv("X_ACCESS_TOKEN"),
    access_token_secret=os.getenv("X_ACCESS_SECRET"),
)

resp = client.create_tweet(text="Hola mundo desde AgroBot 🚜")
print("Publicado:", resp)
