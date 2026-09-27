import React, { useState, useEffect } from 'react';
import './Dropdown.css';
import { getTopics } from '../../data/getTopics.js';

function Dropdown({selected, onChange }) {
    const [options, setOptions] = useState([]); // State to store events data
    
      useEffect(() => {
        const fetchTopics = async () => {
          const data = await getTopics();
          setOptions(data);
        };
    
        fetchTopics();
      }, []);
      
  return (
    <div className="dropdown">
      <label htmlFor="topic-select">Select Topic:</label>
      <select
        id="topic-select"
        value={selected}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">-- Select a Topic --</option>
        {options.map((topic, index) => (
          <option key={topic.topic_id} value={topic.topic_id}>
            {topic.topic_name}
          </option>
        ))}
      </select>
    </div>
  );
}

export default Dropdown;