import React, { useState, useEffect, useRef } from 'react';
import './Persona.css';
import { getLegendData } from '../../data/getLegendData.js'; // Importing your events.json data
import { sort } from 'd3';
const icons = import.meta.glob('./icons/*.png', { eager: true });
const images = import.meta.glob('./img/*.png', { eager: true });

const Persona = ({ onFilterChange, innerWidth, filter, topic_id }) => {
  const [widgets, setWidgets] = useState([]);
  const [selectedWidget, setSelectedWidget] = useState(null);
  const [loading, setLoading] = useState(true);
  const [events, setEvents] = useState([]); // State to store events data
  const gridRef = useRef(null); // Ref for the persona-grid

  useEffect(() => {
    const fetchEvents = async () => {
      setLoading(true);
      const data = await getLegendData(topic_id);
      if (data && data.events) {
        setEvents(data.events);
      } else {
        console.error("eventsData is undefined in the fetched data:", data);
      }
      setLoading(false);
    };
    fetchEvents();
  }, [topic_id]);

  useEffect(() => {
    // Create a map for each person's earliest date and party
    const nameToDetails = events.reduce((acc, event) => {
      const eventDate = new Date(event.article_date);
      if (!acc[event.person_name]) {
        acc[event.person_name] = {
          date: eventDate,
          political_party: event["political_party"],
          consistency: event["inconsistency_flag"] == 1 ? "inconsistent" : "trusted",
          stance: event["stance"] || "for",
          summary: event.person_summary,
          // Store the image URL from the event data
          imageUrl: event.image_url || null,
        };
      } else {
        acc[event.person_name].political_party = event["political_party"] || acc[event.person_name].political_party;
        acc[event.person_name].stance = event["stance"] || acc[event.person_name].stance;
        // Update image URL if available and not previously set
        if (event.image_url && !acc[event.person_name].imageUrl) {
          acc[event.person_name].imageUrl = event.image_url;
        }
        if (event["inconsistency_flag"] == 1) {
          acc[event.person_name].consistency = "inconsistent";
        }
      }
      return acc;
    }, {});

    const sortedNames = Object.keys(nameToDetails).sort(
      (a, b) => new Date(nameToDetails[a].date) - new Date(nameToDetails[b].date)
    );

    setWidgets(
      sortedNames.map((name) => ({
        name,
        political_party: nameToDetails[name].political_party,
        consistency: nameToDetails[name].consistency,
        stance: nameToDetails[name].stance,
        summary: nameToDetails[name].summary,
        imageUrl: nameToDetails[name].imageUrl, // Include the image URL in the widget data
      }))
    );
  }, [events]);

  const handleWidgetClick = (name) => {
    if (selectedWidget === name) {
      setSelectedWidget(null); // Deselect widget if already selected
      onFilterChange(null); // Clear the filter
    } else {
      setSelectedWidget(name); // Set clicked widget as selected
      onFilterChange(name); // Pass selected name to filter handler
    }
  };

  // Update selected widget based on filter
  useEffect(() => {
    setSelectedWidget(filter);
  }, [filter]);

  const defaultWidth = innerWidth / widgets.length; // Calculate default widget width

  // Add horizontal scroll on wheel event
  useEffect(() => {
    const el = gridRef.current;
    if (el) {
      const handleWheel = (e) => {
        if (e.deltaY !== 0) {
          e.preventDefault();
          el.scrollLeft += e.deltaY;
        }
      };

      el.addEventListener('wheel', handleWheel);

      // Cleanup function to remove the event listener
      return () => {
        el.removeEventListener('wheel', handleWheel);
      };
    }
  }, []);

  return (
    <div className="persona-grid" ref={gridRef} style={{ width: `${innerWidth}px` }}>
      {widgets.map((widget, index) => {
        const partyIconPath = icons[`./icons/${widget.political_party}.png`]?.default || null;
        const consistencyIconPath = icons[`./icons/${widget.consistency}.png`]?.default || null;
        // Use image URL from database or a default image as fallback
        const defaultImageUrl = images[`./img/${widget.name}.png`]?.default || images[`./img/default.png`]?.default; // Placeholder image
        const imageUrl = widget.imageUrl || defaultImageUrl;

        return (
          <div
            key={index}
            className={`widget ${selectedWidget === widget.name ? 'expanded' : selectedWidget ? 'shrunk' : 'default'}`}
            onClick={() => handleWidgetClick(widget.name)}
          >
            <div className="widget-content">
              <div className="widget-left">
                {/* Use the image URL directly */}
                <img src={imageUrl} alt={widget.name || 'Default'} className="widget-img" />
                <div className="icon-container">
                  {partyIconPath !== null ? (
                    <img src={partyIconPath} alt={widget.political_party} className="icon-party" />
                  ) : (
                    <div className="icon-placeholder"></div>
                  )}
                  <div className={`stance ${widget.stance.toLowerCase()}`}>{widget.stance}</div>
                  {consistencyIconPath !== null ? (
                    <img src={consistencyIconPath} alt={widget.consistency} className="icon-consistency" />
                  ) : (
                    <div className="icon-placeholder"></div>
                  )}
                </div>
                <div className="widget-name">{widget.name}</div>
              </div>
              {selectedWidget === widget.name && (
                <div className="widget-right">
                  <div className="widget-text">
                    <p>{widget.summary}</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default Persona;
