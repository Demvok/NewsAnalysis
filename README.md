#### TO DO:

- make model usage dashboard
- fix person duplication because of different model outputs
- optimize scraper, paralellise if possible


#### Time usage:

##### Scrap:
1. ~20 articles = 1 page of 1 topic = ~74 chunks (40m)
2. 1 page of 1 topic = 27m
3. 5 pages of 1 topic = 101 articles = 1hr 27m (4-26-26-23-8 mins for each page) = 324 chunks
4. 3 pages of 3 topics = 258 articles = +- 1 hr = 884 chunks

##### LangGraph:
1. 74 chunks (extraction+sentiment) (26m) = 0.35135 m/ch (stages 0-1)
2. 324 chunks (1hr 53m) = 0.348756 m/ch (stages 0-1)
3. 10 chunks (5m) = 0.5 m/ch (stages 0-2.1)
4. 10 chunks (10m) = 1 m/ch (full)
5. 324? chunks (6h) = 1.11 m/ch (full)
6. 324 chunks (3h 30m) = 40 s/ch (full, parallelized)
7. 50 articles = 142 chunks gemma-3-4b-it-qat = 1hr 45m = 43.88 s/ch - gemma-3-4b-it-qat
8. 50 articles = 142 chunks llama-3.2-3b-instruct = 1hr 22m = 34.78 s/ch
9. 50 articles = 142 chunks qwen3-4b /no_think = 1hr 19m = 33.64 s/ch
10. 50 articles = 142 chunks gemma-3-4b-it = 1hr 37m = 40.90 s/ch