import React, { useState, useEffect } from 'react';
import "./EventTimeline.css";
import { getEvents } from '../../data/getEvents';

function EventTimeline({ width, onEventBoxClick, topic_id }) {
  const [generalEvents, setEventsData] = useState([]); // State to store events data

  useEffect(() => {
    const fetchEvents = async () => {
      const generalEvents = await getEvents(topic_id);
      setEventsData(generalEvents);
    };

    fetchEvents();
  }, [topic_id]);

  

  
  const sortedEvents = generalEvents.sort(
    (a, b) => new Date(a.date) - new Date(b.date)
  );

  

  return (
    <div className="EventTimeline" style={{ width: width }}>
      {sortedEvents.map((event, index) => {
        return (
          <div
            className="EventBox"
            key={index}
            onClick={() => onEventBoxClick(index)} // Передаємо індекс вибраного елемента
          >
            <p className="EventBox-title">{event.title}</p>
            <p className="EventBox-date">{event.date.split('T')[0]}</p>
            <p className="EventBox-description">{event.description}</p>
            <a
              className="EventBox-readmore"
              href={event.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Read more
            </a>
          </div>
        );
      })}
    </div>
  );
}

export default EventTimeline;