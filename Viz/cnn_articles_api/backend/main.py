from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routes import article, opinion, person, topic, events, attitude

app = FastAPI()



# Дозволяє доступ з усіх доменів (можна обмежити список за потреби)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Можна вказати конкретні домени, наприклад ["https://your-frontend-domain.com"]
    allow_credentials=True,
    allow_methods=["*"],  # Дозволяються всі HTTP-методи
    allow_headers=["*"],  # Дозволяються всі заголовки
)


app.include_router(article.router, prefix="/articles", tags=["Articles"])
app.include_router(opinion.router, prefix="/opinions", tags=["Opinions"])
app.include_router(person.router, prefix="/persons", tags=["Persons"])
app.include_router(topic.router, prefix="/topics", tags=["Topics"])
app.include_router(events.router, prefix="/events", tags=["Events"])
app.include_router(attitude.router, prefix="/attitude", tags=["Attitude"])
