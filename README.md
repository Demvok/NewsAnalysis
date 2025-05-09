#### TO DO:

- optimize scraper, paralellize if possible
- fine tune event extraction, there are too many of them
- fix person duplication because of different model outputs
- add usege type to `llm_invoke` for better traking
- add cleaner for logs


#### Time usage:

##### Scrap:
1. ~20 articles = 1 page of 1 topic = ~74 chunks (40m)
2. 1 page of 1 topic = 27m
3. 5 pages of 1 topic = 101 articles = 1hr 27m (4-26-26-23-8 mins for each page) = 324 chunks

##### LangGraph:
1. 74 chunks (extraction+sentiment) (26m) = 0.35135 m/ch
2. 324 chunks (1hr 53m) = 0.348756 m/ch

