const API_URL = import.meta.env.VITE_APP_API_URL;

// src/data/getChartData.js
export async function getLegendData(topic_id=1) {

  try {
    const [opinionsRes, personsRes, articlesRes, attitudeRes] = await Promise.all([
      fetch(`${API_URL}opinions/?is_selected=1`),
      fetch(`${API_URL}persons`),
      fetch(`${API_URL}articles`),
      fetch(`${API_URL}attitude`)
    ]);

    if (!opinionsRes.ok || !personsRes.ok || !articlesRes.ok || !attitudeRes.ok) {
      throw new Error("Failed to fetch data from one or more endpoints");
    }

    const [opinions, persons, articles, attitudes] = await Promise.all([
      opinionsRes.json(),
      personsRes.json(),
      articlesRes.json(),
      attitudeRes.json()
    ]);

    const merged = opinions.map(opinion => {
      const person = persons.find(p => p.person_id === opinion.fk_person_id);
      const article = articles.find(a => a.article_id === opinion.fk_origin_article_id); // Фільтруємо за темою, якщо вона задана
      const attitude = attitudes.find(a => a.fk_person_id === opinion.fk_person_id);
      
      return {
        ...opinion,
        person_name: person?.person_name || "Unknown",
        political_party: person?.political_party || "Unknown",
        article_title: article?.title || "Unknown",
        article_url: article?.url || "#",
        article_date: article?.article_date && !isNaN(new Date(article.article_date).getTime())
          ? new Date(article.article_date).toISOString().split("T")[0]
          : "Unknown",
        topic_id: article?.fk_topic_id || "Unknown",
        person_summary: attitude?.person_summary || "No summary available",
        stance: attitude?.stance || "Unknown",
        // Include image_url from the person data
        image_url: person?.image_url || null,
      };
    }).filter(item => item.topic_id == topic_id && item.person_name != 'Unspecified');

    // Групування даних по персоні
    const groupedByPerson = merged.reduce((acc, item) => {
      const personName = item.person_name;
      if (!acc[personName]) {
        acc[personName] = [];
      }
      acc[personName].push(item);
      return acc;
    }, {});

    console.log("groupedByPerson length:", Object.keys(groupedByPerson).length);
    


    
    return {events: Object.values(groupedByPerson).flat()}; // Повертаємо згруповані дані.slice(1, 50)
  } catch (error) {
    console.error("Error in getLegendData:", error);
    return {}; // Повертаємо порожній об'єкт у разі помилки
  }
};