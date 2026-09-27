import { useEffect, useRef, useState } from "react";
import Chart from "chart.js/auto";
import "chartjs-adapter-date-fns";
import { getChartData } from "../data/getChartData";
import LegendPanel from "./LegendPanel";
import tooltipStyles from "../styles/tooltip.module.css";
import annotationPlugin from "chartjs-plugin-annotation";
Chart.register(annotationPlugin);


const ChartWidget = () => {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const [legendData, setLegendData] = useState([]);
  const [groupedData, setGroupedData] = useState({});
  const [generalEvents, setGeneralEvents] = useState([]);
  const [filteredPersons, setFilteredPersons] = useState([]);

  useEffect(() => {
    const load = async () => {
      const { groupedData, legendData, generalEvents } = await getChartData();
      setLegendData(legendData);
      setGroupedData(groupedData);
      setGeneralEvents(generalEvents);
      updateChart(groupedData, legendData, generalEvents);
    };
    load();
  }, []);

  useEffect(() => {
    updateChart(groupedData, legendData, generalEvents, filteredPersons);
  }, [filteredPersons, groupedData, generalEvents, legendData]);

  const updateChart = (groupedData, legendData, generalEvents, filters = []) => {
    const datasets = legendData
      .filter(person => filters.length === 0 || filters.includes(person.name))
      .map(person => ({
        label: person.name,
        data: groupedData[person.name],
        borderColor: person.color, 
        backgroundColor: person.color, 
        tension: 0.5,
        fill: false,
        pointRadius: ctx => ctx.raw.r,
      }));

    console.log(datasets);

    
    if (chartRef.current) chartRef.current.destroy();
    const ctx = canvasRef.current.getContext("2d");    
    chartRef.current = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          tooltip: {
            enabled: false, // вимикаємо стандартний Canvas тултіп
            external: function(context) {
              const tooltipModel = context.tooltip;
            
              let tooltipEl = document.getElementById('chartjs-tooltip');
              if (!tooltipEl) {
                tooltipEl = document.createElement('div');
                tooltipEl.id = 'chartjs-tooltip';
                tooltipEl.className = tooltipStyles.tooltip;
            
                // Початкове положення та події
                tooltipEl.addEventListener('mouseenter', () => {
                  tooltipEl.setAttribute('data-hover', 'true');
                });
                tooltipEl.addEventListener('mouseleave', () => {
                  tooltipEl.setAttribute('data-hover', 'false');
                  setTimeout(() => {
                    if (tooltipEl.getAttribute('data-hover') === 'false') {
                      tooltipEl.classList.remove(tooltipStyles.show);
                    }
                  }, 300);
                });
            
                document.body.appendChild(tooltipEl);
              }
            
              if (tooltipModel.opacity === 0) {
                // затримка перед зникненням, якщо миша не на тултіпі
                setTimeout(() => {
                  if (tooltipEl.getAttribute('data-hover') !== 'true') {
                    tooltipEl.classList.remove(tooltipStyles.show);
                  }
                }, 300);
                return;
              }
            
              // Отримуємо дані
              const raw = tooltipModel.dataPoints?.[0]?.raw;
              const tooltipData = raw?.tooltip;
            
              // Відображення контенту
              tooltipEl.innerHTML = `
                <div><strong>${tooltipData?.person}</strong></div>
                <div style="margin-top: 5px; white-space: pre-line;">"${tooltipData?.quote}"</div>
                <div style="margin-top: 8px;"><strong>Article:</strong> ${tooltipData?.title}</div>
                <div><a href="${tooltipData?.url}" target="_blank">Read more</a></div>
              `;
            
              const { offsetLeft: chartX, offsetTop: chartY } = context.chart.canvas;
              
              const tooltipWidth = 300; // Ширина тултіпу
              const pageWidth = window.innerWidth;
              

              // Базова позиція (праворуч від курсора)
              let left = chartX + tooltipModel.caretX - 10;

              // Якщо елемент далі, ніж 75% екрану — зсунути тултіп ліворуч
              if (tooltipWidth + tooltipModel.caretX > pageWidth * 0.75) {
                console.log(tooltipModel.caretX);
                
                left = tooltipModel.caretX - tooltipWidth + 12;
              }

              let top = chartY + tooltipModel.caretY;

              tooltipEl.style.left = `${left}px`;
              tooltipEl.style.top = `${top}px`;
            
              tooltipEl.classList.add(tooltipStyles.show);
            }
          },
          legend: { display: false },
          annotation: {
            annotations: {
              zeroLine: {
                type: "line",
                yMin: 0,
                yMax: 0,
                borderColor: "rgba(0, 0, 0, 0.8)", // Колір лінії
                borderWidth: 2, // Товщина лінії
                borderDash: [10, 5], // Пунктир (10px лінія, 5px пробіл)
              },
            },
          },
        },
        scales: {
          x: {
            type: "time",
            time: { unit: "day" },
            title: { display: true, text: "Date" },
            grid: { display: false },
          },
          y: {
            title: { display: true, text: "Sentiment Score" },
            beginAtZero: true,
            min: -1, // Мінімальне значення
            max: 1,  // Максимальне значення
            grid: { display: false },
          },
        },
      },
    });


    setTimeout(() => {
      const container = document.getElementById("event-container");
      if (!container || !chartRef.current) return;
    
      container.innerHTML = ""; // Очистити старі події
    
      const chart = chartRef.current;
      const scale = chart.scales.x;
      const chartBox = chart.canvas.getBoundingClientRect();
    
      generalEvents.forEach((event, i) => {
        const x = scale.getPixelForValue(event.date);
    
        const eventBlock = document.createElement("div");
        eventBlock.style.position = "absolute";
        eventBlock.style.left = `${x - 60}px`; // центрування (половина ширини)
        eventBlock.style.bottom = "10px";
        eventBlock.style.width = "120px";
        eventBlock.style.background = "white";
        eventBlock.style.border = "1px solid #ccc";
        eventBlock.style.borderRadius = "8px";
        eventBlock.style.padding = "6px";
        eventBlock.style.boxShadow = "0 2px 4px rgba(0,0,0,0.1)";
        eventBlock.style.fontSize = "11px";
        eventBlock.style.pointerEvents = "auto"; // дозволити клік, якщо треба
    
        eventBlock.innerHTML = `
          <div style="font-weight:bold">${event.title}</div>
          <div style="margin-top:2px; color:#555">${event.description}</div>
        `;
    
        // Лінія-вказівник
        const line = document.createElement("div");
        line.style.position = "absolute";
        line.style.left = `${x}px`;
        line.style.bottom = "50px"; // нижче шкали
        line.style.height = "40px";
        line.style.width = "1px";
        line.style.backgroundColor = "rgba(0,0,0,0.3)";
    
        container.appendChild(line);
        container.appendChild(eventBlock);
      });
    }, 300);

  };



  function splitText(text, maxLength) {
    const words = text.split(" ");
    const lines = [];
    let currentLine = "";
  
    words.forEach(word => {
      if ((currentLine + word).length < maxLength) {
        currentLine += word + " ";
      } else {
        lines.push(currentLine.trim());
        currentLine = word + " ";
      }
    });
  
    if (currentLine) lines.push(currentLine.trim());
    return lines;
  }

  const handleCardClick = (name) => {
    setFilteredPersons((prev) => {
      if (prev.includes(name)) {
        // Видалити політика, якщо він уже вибраний
        return prev.filter((person) => person !== name);
      } else {
        // Додати політика до вибраних
        return [...prev, name];
      }
    });
  };

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Легенда */}
      <div style={{ flexShrink: 0 }}>
        <LegendPanel
          data={legendData}
          onCardClick={handleCardClick}
          selectedPersons={filteredPersons}
        />
      </div>
      {/* Графік */}
      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />
        <div
          id="event-container"
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            width: "100%",
            pointerEvents: "none", // події миші не перехоплюються
          }}
        ></div>
      </div>
    </div>
  );
};

export default ChartWidget;