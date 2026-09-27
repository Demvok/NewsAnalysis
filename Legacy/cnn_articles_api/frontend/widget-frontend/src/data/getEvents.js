const API_URL = import.meta.env.VITE_APP_API_URL;

export async function getEvents(topic_id) {

  
  
  try {
    // Крок 1: Отримуємо список статей
    const articlesResponse = await fetch(`${API_URL}articles/`);
    if (!articlesResponse.ok) {
      throw new Error(`Failed to fetch articles: ${articlesResponse.statusText}`);
    }

    const articles = await articlesResponse.json();

    // Крок 2: Отримуємо список подій
    const eventsResponse = await fetch(`${API_URL}events/?relevance_score=0.8&event_hotness=0.8&is_selected=1&`);
    if (!eventsResponse.ok) {
      throw new Error(`Failed to fetch events: ${eventsResponse.statusText}`);
    }

    const events = await eventsResponse.json();


    // Крок 3: Зв'язуємо події зі статтями за `article_id` та `fk_article_id`
    // Фільтруємо події, залишаючи тільки ті, у яких є стаття з відповідним topic_id
    const eventsWithArticles = events
      .map(event => {
      const relatedArticle = articles.find(
        article => article.article_id === event.fk_origin_article_id && article.fk_topic_id == topic_id
      );
      if (!relatedArticle) return null; 

      return {
        id: event.event_id,
        title: event.event_title,
        topic_id: relatedArticle.fk_topic_id,
        description: event.description,
        relevanceScore: event.relevance_score,
        date: relatedArticle.article_date,
        url: relatedArticle.url,
        article: relatedArticle.title,
      };
      })
      .filter(Boolean); // Видаляємо null (тобто ті події, для яких не знайшли статтю)
        
    
      // .filter(event => new Date(event.date) < new Date('2024-05-16'))
      //.slice(0, 10);


    

    return eventsWithArticles;
  } catch (error) {
    console.error('Error fetching events:', error);
    return [];
  }
};