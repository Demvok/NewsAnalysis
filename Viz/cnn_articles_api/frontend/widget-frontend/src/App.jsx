import React, { useState, useEffect, useRef } from 'react';
import * as d3 from 'd3'; // Import D3 library
import { getChartData } from './data/getChartData.js'; // Importing your events.json data
import './App.css'; // Import CSS for the pulsing effect
import './components/BubbleChart/BubbleChart.css'; // Import CSS for the pulsing effect
import Dropdown from './components/Dropdown/Dropdown';
import EventTimeline from './components/EventTimeline/EventTimeline'; // Import the new component
import { getRadiusBasedOnInfluence, getOpacityBubbles } from './utils';
import Tooltip from './components/Tooltip/Tooltip';
import XAxis from './components/XAxis/XAxis';
import EventBoxConnectorLines from './components/EventBoxConnectorLines/EventBoxConnectorLines';
import BubbleLines from './components/BubbleLines/BubbleLines';
//import Persona from './components/Persona/Persona'; // Adjust path according to your structure
import Persona from './components/Persona/Persona alt'; // Adjust path according to your structure
import YAxis from './components/YAxis/YAxis';
import Legend from './components/Legend/BubbleLegend';

function BubbleChart() {
  const [selectedEventIndex, setSelectedEventIndex] = useState(null);
  const [hoveredEvent, setHoveredEvent] = useState(null);
  const [lastHoveredEvent, setLastHoveredEvent] = useState(null);
  const [isTooltipHovered, setIsTooltipHovered] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState(null); // State to track the selected event
  const [filter, setFilter] = useState(null); // State to track the selected filter
  const [eventsData, setEventsData] = useState([]); // State to store events data
  const appRef = useRef(null); // Ref for the persona-grid
  const [selectedTopic, setSelectedTopic] = useState(1); // State for selected topic



  useEffect(() => {
    const fetchChartData = async () => {
      const data = await getChartData(selectedTopic); // Fetch data based on selected topic
      if (data && data.eventsData) {
        setEventsData(data.eventsData);

      }
    };
    if (selectedTopic) {
      fetchChartData();

    }
  }, [selectedTopic]);




  const [uniqueDates, setUniqueDates] = useState([]);
  const [width, setWidth] = useState(0);
  const height = 600;
  const margin = { top: 20, right: 50, bottom: 150, left: 70 };
  const [innerWidth, setInnerWidth] = useState(0);
  const [innerHeight, setInnerHeight] = useState(0);
  const [nodes, setNodes] = useState([]); // State to store nodes for the force simulation
  const [xScale, setXScale] = useState(null); // State to store the X scale
  const [dateIndexMap, setDateIndexMap] = useState({}); // State to store the date index mapping
  const [svgRef, setSvgRef] = useState(null); // State to store the SVG reference
  const [yScale, setYScale] = useState(null); // State to store the Y scale

  useEffect(() => {


    
    const dates = [...new Set(eventsData.map(d => d.article_date))];

    setUniqueDates(dates);

    const calculatedWidth = (dates.length + 2) * 100;
    setWidth(calculatedWidth);
    setInnerWidth(calculatedWidth - margin.left - margin.right);
    const innerHeight = height - margin.top - margin.bottom;
    setInnerHeight(innerHeight);



    setSvgRef(null);

    // Create Y scale to map sentiment_score (-1.5 to 1.5) to a positive range
    // Map the Y-axis from top (1.5) to bottom (-1.5)
    const YScale = d3.scaleLinear()
      .domain([-1.15, 1.15]) // Input domain: sentiment_score range (-1.5 to 1.5)
      .range([innerHeight, 0])

    setYScale(() => YScale); // Передаємо функцію

    // Function to prepare force simulation for clustering bubbles
    const createForceSimulation = (initialNodes, xScale, yScale, getRadiusBasedOnInfluence) => {
      const simulation = d3.forceSimulation(initialNodes)
        .force("x", d3.forceX(d => xScale(d.dateIndex)).strength(0.8))
        .force("y", d3.forceY(d => yScale(d.sentiment_score)).strength(1))
        .force("collision", d3.forceCollide(d => getRadiusBasedOnInfluence(d.opinion_hotness) + 2))
        .stop();

      // Tick manually
      for (let i = 0; i < 300; ++i) simulation.tick();

      return initialNodes;
    };



    const allDates = [...new Set(eventsData.map(d => d.article_date))].sort((a, b) => new Date(a) - new Date(b));
    const dateIndexMap = allDates.reduce((acc, date, i) => {
      acc[date] = i;
      return acc;
    }, {});


    setDateIndexMap(dateIndexMap);

    const xScale = d3.scaleLinear()
      .domain([0, allDates.length - 1])
      .range([50, allDates.length * 100]);

    setXScale(() => xScale); // Передаємо функцію

    const preparedNodes = eventsData.map(event => ({
      ...event,
      dateIndex: dateIndexMap[event.article_date],
      x: 0,
      y: yScale(event.sentiment_score),
    }));



    const simulatedNodes = createForceSimulation(preparedNodes, xScale, yScale, getRadiusBasedOnInfluence);
    setNodes(simulatedNodes);


  }, [eventsData]);














  const bubbleOpacities = nodes.map(event =>
    getOpacityBubbles(event, hoveredEvent, selectedEvent, filter)
  );

  // Scroll to the selected bubble based on Persona widget selection
  useEffect(() => {


    if (filter && appRef.current) {
      const selectedBubble = nodes.find(event => event.name === filter);
      if (selectedBubble) {
        // Calculate bubble position within the SVG
        const svgRect = svgRef.current.getBoundingClientRect();
        const bubbleX = window.scrollX + svgRect.left + margin.left + selectedBubble.x;

        // Smooth scroll to bring the bubble to the center of the viewport
        window.scrollTo({
          left: bubbleX - window.innerWidth / 2,
          behavior: 'smooth',
        });
      }
    }
  }, [filter, nodes, margin.left]);


  useEffect(() => {
    const appElement = appRef.current;
    if (appElement) {
      const handleWheel = (e) => {
        if (e.deltaY !== 0) {
          e.preventDefault();
          appElement.scrollLeft += e.deltaY; // Горизонтальна прокрутка
        }
      };

      appElement.addEventListener('wheel', handleWheel);

      return () => {
        appElement.removeEventListener('wheel', handleWheel);
      };
    }
  }, []);


  return (
    <div className="App" ref={appRef} style={{ overflowX: 'auto', overflowY: 'hidden' }}>
      <div className="Persona">
        <h1></h1>
        {<Persona onFilterChange={setFilter} innerWidth={innerWidth} filter={filter} topic_id={selectedTopic} />}
      </div>
      <div className="Legend">
        <Legend />
      </div>

      <div className='SvgTooltipWrapper'>
        <Dropdown
          selected={selectedTopic}
          onChange={setSelectedTopic}
          style={{
            position: 'absolute', // Розташування поверх графіка
            top: '-20px', // Зміщення вниз (регулюйте за потреби)
            left: '20px', // Зміщення вправо (регулюйте за потреби)
            zIndex: 10, // Вищий пріоритет для відображення поверх графіка
          }}
        />
        {/* SVG without overflow styling */}
        <svg
          ref={svgRef}
          width={width}
          height={height}
          onClick={(ev) => {
            // if clicked outside of the bubbles, deselect the selected event
            if (!ev.target.classList.contains('bubble-main')) {
              setSelectedEvent(null);
              setFilter(null);
            }

            // find event with event id from data-event-id attribute of the clicked element and set it as selected event
            const event = nodes.find(d => d.opinion_id === Number(ev.target.dataset.eventId));


            if (event) {
              setSelectedEvent(event);
              setHoveredEvent(event); // also set hovered event to show tooltip immediately (otherwise it would only show on mouseout and mouseover again)
              setFilter(event.person_name); // clear the filter or set it to the persona name setFilter(event.name)
            }
          }}
          onMouseOut={(ev) => {
            setHoveredEvent(null);

          }}
          onMouseOver={(ev) => {
            // if mouse is over the SVG, set the last hovered event to null
            if (!ev.target.classList.contains('bubble-main')) {
              setLastHoveredEvent(null);
            }
          }}
        >
          <YAxis innerHeight={innerHeight} innerWidth={innerWidth} margin={margin} />
          <g transform={`translate(${margin.left}, ${margin.top})`}>
            {/* Render unique date labels and axis line */}
            <XAxis chartData={nodes} innerHeight={innerHeight} innerWidth={innerWidth} />

            <BubbleLines chartData={nodes} hoveredEvent={hoveredEvent} selectedEvent={selectedEvent} filter={filter} />

            {/* Render bubbles */}
            <g className="bubbles">
              {nodes.map((event, index) => (
                /* Fade non-relevant bubbles based on the 'name' field */
                <g
                  key={index}
                  className="bubbleGroup"
                  opacity={bubbleOpacities[index]}
                >
                  {/* Pulsing effect if inconsistency is TRUE */}

                  {event.inconsistency_flag === 1 && (
                    <>
                      <circle
                        className="pulsing"
                        cx={event.x}
                        cy={event.y}
                        r={getRadiusBasedOnInfluence(event.opinion_hotness)}
                        fill="orange"
                        opacity={0.5}
                        style={{ transformOrigin: `${event.x}px ${event.y}px`, animationDelay: '1s' }}
                      />
                    </>
                  )}

                  {nodes.filter((e) => e.inconsistency_with_id?.includes(`${event.opinion_id}`)).length > 0 && (
                    <>
                      {<circle
                        className="pulsing"
                        cx={event.x}
                        cy={event.y}
                        r={getRadiusBasedOnInfluence(event.opinion_hotness)}
                        fill="deepskyblue"
                        opacity={0.5}
                        style={{ transformOrigin: `${event.x}px ${event.y}px`, animationDelay: '0s' }}
                      />}

                    </>

                  )}
                  {/* Normal bubble */}
                  <circle
                    cx={event.x} // Bubble X position from force simulation
                    cy={event.y} // Set Y position from force simulation
                    r={getRadiusBasedOnInfluence(event.opinion_hotness)} // Set radius based on "Overall influence"
                    fill={hoveredEvent === null || event.person_name === hoveredEvent.person_name ? 'darkblue' : 'darkblue'} //#b7c9e2
                    opacity={1}
                    onMouseOver={() => {
                      // only show tooltip if no event is selected or if the hovered event is the same as the selected event
                      // if(selectedEvent === null || selectedEvent.person_name === event.person_name) {
                      //    // Store the last hovered event
                      // }
                      setHoveredEvent(event);
                      setLastHoveredEvent(event);
                    }}
                    onMouseOut={() => {
                      setHoveredEvent(null);
                      // if (!isTooltipHovered) {
                      //   setLastHoveredEvent(null); // Hide tooltip when mouse leaves
                      // }

                    }}

                    className='bubble-main'
                    data-event-id={event.opinion_id} // Add data attribute to identify the event
                  // onClick={() => {}} --> on click event is handled on the SVG element
                  >
                  </circle>
                  <h1 className="bubble-label" x={event.x} y={event.y} opacity={1} textAnchor="middle" dy=".3em">{event.headline}</h1>
                </g>
              ))}
            </g>

            {/* Render connector lines between x axis and event boxes in timeline */}
            <EventBoxConnectorLines
              xScale={xScale}
              innerHeight={innerHeight}
              dateIndexMap={dateIndexMap}
              innerWidth={innerWidth}
              selectedEventIndex={selectedEventIndex} // Передаємо вибраний індекс
              topic_id={selectedTopic} // Передаємо topic_id для фільтрації
            />
          </g>
        </svg>
        {/* Render tooltip when hovered or when a filter is applied */}
        {hoveredEvent || lastHoveredEvent ? (
          <Tooltip
            hoveredEvent={lastHoveredEvent}
            margin={margin}
            onMouseEnter={() => setIsTooltipHovered(true)} // Keep tooltip visible
            onMouseLeave={() => {
              setLastHoveredEvent(null); // Дозволяємо приховати тултіп
              setIsTooltipHovered(false); // Сховати тултіп при наведенні на нього
            }}
          />
        ) : null}
      </div>

      {/* Render the EventTimeline component */}
      <EventTimeline
        width={width}
        onEventBoxClick={(index) => {
          if (selectedEventIndex === index) {
            setSelectedEventIndex(null);
          } else {
            setSelectedEventIndex(index);
          }
        }} // Оновлюємо вибраний індекс
        topic_id={selectedTopic} // Передаємо topic_id для фільтрації
      />
    </div>
  );
}

export default BubbleChart;