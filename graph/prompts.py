REFINING_PROMPT = "Refine the following text to fit within {max_length} characters:\n\n{field_value}"

EVENT_CLASSIFICATION_PROMPT = """
You are an expert journalist assistant. Your task is to extract:

1. General events related to the topic "{topic}" from the article.
2. Person events related to someone’s statement or action regarding "{topic}".

Ensure the following constraints:
- The "title" of the general event must not exceed 50 characters.
- The "description" of the general event must not exceed 200 characters.
- The "person_name" must not exceed 150 characters.
- The "citation" must not exceed 300 characters.

Without superfluous information, just the most important details. Provide only the anwear, do not include any additional text or explanations.

Return a JSON object in the following format:

{{
"general_event": {{
    "title": "...",
    "description": "..."
}},
"person_event": {{
    "person_name": "...",
    "citation": "..."
}}
}}

If no event is found, use `null`.

Article:
{chunk}
"""

SENTIMENT_ANALYSIS_PROMPT = """
You are a debate and sentiment analysis expert.  
Given the following topic and text, return **only** a single floating-point number between -1.0 and +1.0, where:
- -1.0 indicates strongly negative sentiment  
-  0.0 indicates neutral sentiment  
- +1.0 indicates strongly positive sentiment  

Topic: {topic}  
Text:  
{content}

Respond with the number alone (e.g. “-0.75”).
"""

INCONSISTENCY_COMMENT_PROMPT = """
You are an expert journalist skilled in deduction and speech analysis.  
Given the following inputs, compare the new citation to previous ones and comment on any inconsistency.

Person: {person}  
Topic: {topic} 
New citation:  
- Text: {new_citation}  
- Date: {new_date}  
- Score: {new_score}  
Previous citations (up to 15):  
{previous_formatted}

Write **only one paragraph**, max **200 characters**, pointing out the sentiment inconsistency. Do not include quotes or metadata—just the concise comment.
"""

PERSON_SUMMARY_PROMPT = """
You are an expert journalist proficient in analyzing political stances. Given the following inputs, produce **only** the final person_summary text (max 300 characters) that:

1. Summarizes the person’s current position on the topic.
2. Notes their expert status and openness to change.
3. Highlights any recent contradiction, if present.
4. Integrates key details from the new citation and, if available, the previous summary.

If a previous summary exists, should make an updated version of it.  
Respond with no bullet points, no options, no additional commentary—just the **single person_summary string** of given length. Do **not** start with typical phrases like "Here's a summary under 300 characters:", start already with option text. Output **only** the most full option's text.

Inputs:
Person: {person}  
Is expert on given topic: {is_expert}  
Topic: {topic}  
Citation: {citation}  
Stance: {stance}  
Tendency to change stance: {sentiment_deviation}  
{{#if previous_summary}}
Previous summary: {previous_summary}  
{{/if}}
{{#if inconsistency}}
Recently found inconsistency: {inconsistency}  
{{/if}}
"""

OPINION_SCORING_PROMPT = """
You are an expert journalist proficient in analyzing political stances. Given the following inputs, score the opinion (citation) on the topic.:
Make 3 scores:
1. Relevancy: 0-1 floating-point number (how relevant the opinion is to the topic)
2. Contribution: 0-1 floating-point number (how much the opinion contributes to the topic)
3. Controversy: 0-1 floating-point number (how controversial the opinion is)
Use 0 if score in close to useless, 1 if score is absolutely useful. Be as precise as possible.

Provide **only** the scores in the following format:
{{
    "relevancy": <relevancy>,
    "contribution": <contribution>, 
    "controversy": <controversy>
}}

Inputs:
Topic: {topic}
Person: {person}  
Person summary: {person_summary}
Tendency to change stance: {sentiment_deviation}
Citation: {citation}
Recent inconsistesies found: {inconsistency_flag}
{{#if inconsistency}}
Found inconsistency: {inconsistency}  
{{/if}}  
"""







