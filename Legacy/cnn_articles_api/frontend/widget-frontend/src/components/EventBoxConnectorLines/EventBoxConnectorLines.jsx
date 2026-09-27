import React, { useRef, useEffect, useState } from "react";
import { getEvents } from '../../data/getEvents';
import * as d3 from "d3";

function EventBoxConnectorLines({ xScale, innerHeight, dateIndexMap, innerWidth, selectedEventIndex, topic_id }) {

  

  const [events, setEvents] = useState([]);
  const randomOffsetsRef = useRef([]);

  useEffect(() => {
    const fetchEvents = async () => {
      const events = await getEvents(topic_id);
      setEvents(events);
    };

    fetchEvents();
  }, [topic_id]);

  useEffect(() => {
    if (randomOffsetsRef.current.length === 0 && events.length > 0) {
      randomOffsetsRef.current = events.map(() => Math.random() * 10 - 5); // random offset between -5 and 5
    }
  }, [events]);

  const getClosestDates = (targetDate) => {
    
    const dates = Object.keys(dateIndexMap)
      .map(date => new Date(date))
      .sort((a, b) => a - b);
    const target = new Date(targetDate);

    const smallerDate = dates.reduce((prev, curr) => (curr < target ? curr : prev), dates[0]);
    const largerDate = dates.find(date => date > target);

    return [smallerDate, largerDate];
  };

  const boxWidth = 150;
  const boxGap = 20;

  const lineData = events.map((event, index) => {
    const randomOffset = randomOffsetsRef.current[index];
    if (!randomOffset) {
      return null;
    }

    let startPos = null;
    let endPos = null;

    
    let xDatePos = xScale(dateIndexMap[event.date]);
    if (xDatePos) {
      // const xBoxPos = index * (boxWidth + boxGap) + boxWidth / 2;

      const xBoxPos =  (innerWidth / events.length) * index;
      startPos = { x: xDatePos, y: innerHeight + 30 };
      endPos = { x: xBoxPos, y: innerHeight + 50 };
      
    } else {
      const [smallerDate, largerDate] = getClosestDates(event.date);
      if (smallerDate && largerDate) {

        
        const smallerIndex = dateIndexMap[smallerDate.toISOString().split('T')[0]];
        const largerIndex = dateIndexMap[largerDate.toISOString().split('T')[0]];

        const totalRange = largerDate - smallerDate;
        const dateOffset = new Date(event.date) - smallerDate;
        const normalizedPosition = dateOffset / totalRange;

        xDatePos = xScale(smallerIndex + normalizedPosition * (largerIndex - smallerIndex));

        // const xBoxPos = index * (boxWidth + boxGap) + boxWidth / 2;

        

        const xBoxPos =  (((innerWidth + 70 + 50) / events.length) * (index)) + boxWidth - boxGap - 30;
        
        startPos = { x: xDatePos, y: innerHeight + 30 };
        endPos = { x: xBoxPos, y: innerHeight + 100 };
        
      } else {
        return null;p.lineTo(endPos.x, startPos.y + 50 + randomOffset);
      }
    }

    const p = new d3.path();
    p.moveTo(startPos.x, startPos.y);
    p.lineTo(startPos.x, startPos.y + 10 - randomOffset);
    p.lineTo(endPos.x, startPos.y + 10 - randomOffset);
    p.lineTo(endPos.x, endPos.y);

    

    return {
      pathString: p.toString(),
      startPos,
      endPos,
      opacity: selectedEventIndex === null ? 0.7 : selectedEventIndex === index ? 1 : 0.1, // Логіка прозорості
    };
  });

  return (
    <g className="EventBoxConnectorLines">
      {lineData.map((line, index) => {
        if (line) {
          return (
            <path
              key={index}
              d={line.pathString}
              stroke="black"
              fill="none"
              opacity={line.opacity} // Встановлюємо прозорість
              data-index={index}
            />
          );
        }
        return null;
      })}
    </g>
  );
}

export default EventBoxConnectorLines;
