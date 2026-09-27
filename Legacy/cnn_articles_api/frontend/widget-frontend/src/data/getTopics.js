const API_URL = import.meta.env.VITE_APP_API_URL;

export async function getTopics() {
    
    try {
      const response = await fetch(`${API_URL}topics`);
      if (!response.ok) {
        throw new Error('Failed to fetch topics');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching topics:', error);
      return [];
    }
  }