Existing analysis pipeline is slow and unoptimal. Please rework it according to this, do not care about legacy and backward compatibility.
First - group uncertain vague sources (An expert, Industry sources, Experts, Some, People in the US, Others, Other developers, Those behind AI data centres) as much as possible. They should be analyzed in one batch, like giving all citations for one person in the article at the same time, so all "experts" and so should have similar treatment. I do not know what method would be optimal - I suggest either using one grouping/sorting LLM call, simple clustering using embeddings via API call, or simply shoving all single-citation or small number in one batch. Though you can provide your own idea. I also think prompts should also adapt to it. Goal is to minimize LLM calls as much as possible.
Second - Try to adapt citation analysis and topic evaluation to be a single call. Basically, I want it to clearly decide on topic and what it should mean, search the DB for it and then form an attitude. It it can be made through thinking in one call - it should be it. If it is not optimal, halt it for now.
Thirdly - try to paralellise LLM calls. I do not have necessary capabilities, so it should be togglable, but I want to try. I was thinking on having citation analysis paralellised if it is possible.
Next, already mentioned uncertain and vague speakers should not be tracked for inconsistency at all (not, that company's or government position still should) and have only basic analysis (sort of mention in the prompt to drop heavy thinking if that's the case, provide only basics).
Next, like uncertain speakers, citations with `mention` type, or having no usable source text, or having low attribution confidence, or when citation does not include attributable position, or when extracted name is not an entity - system should treat them with less priority. Drop what's not an actual citation (no source, low confidence, no attributable), work with less thinking on mentions or non-entity citations (opinion of the crowd is still interesting, but just in general)
You may use this list as speaker type, though that's just an actual example `canonical person ,organization/government, collective/group, vague source, unresolved reference`.
Keep semantic search broad, as you requested, but apply cheap checks first: reject below-threshold, similarity; reject missing or invalid dates; reject the current citation; reject duplicate evidence; optionally reject obviously unusable citations.
Then use the LLM only for the strongest candidates. The current four calls are not the main problem, but this will matter as historical data grows.

####

Implement speaker influence evaluation on initial extraction. It should show the impact of the speaker towards mentioned story. If influence score is low it should group speakers like vague and uncertain speakers, so it is processed, but according to importance level. Reject or route non-person speaker entries early:
PERSON: full person/topic/attitude pipeline;
ORGANIZATION: analyze only if explicitly supported;
GROUP: preserve citation, but do not resolve as a person;
ANONYMOUS_SOURCE: preserve evidence only;
UNRESOLVED: do not spend expensive downstream calls until resolved.
This article likely would have avoided several expensive calls.

Make citation extraction refinement optional and with reasoning only in edge cases. Use a staged strategy:
Initial extraction.
Deterministic validation:
empty quotes;
invalid citation type;
missing speaker;
malformed confidence;
duplicate citations.
Only invoke refinement if:
extraction is incomplete;
attribution is uncertain;
quote boundaries are suspicious;
unsupported citation types are present;
citations contain conflicts;
the model explicitly reports uncertainty.

The extraction prompt currently asks for speakers broadly, but the downstream system is designed around public persons. The extraction schema should distinguish:
PERSON
ORGANIZATION
GROUP
ANONYMOUS_SOURCE
UNRESOLVED
Then the workflow can:
preserve non-person citations as evidence;
avoid person-resolution and attitude processing for unsupported source categories;
optionally analyze organizations separately if that becomes an explicit feature.
This could eliminate a large amount of wasted processing for this article.

Tweak thinking mode like so:
Stage	Suggested reasoning mode
Initial extraction	Off
Citation refinement	Off initially; enable only for uncertain cases
Topic extraction	Off
Stance assignment	Off or low reasoning
Inconsistency comparison	On
Ambiguous person resolution	On only when needed
Ambiguous topic canonicalization	On only when needed