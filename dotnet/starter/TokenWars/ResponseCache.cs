namespace TokenWars;

/// <summary>In-memory response caches: exact match (TODO 1.5) and semantic (TODO 1.6). Thread-safe.</summary>
public sealed class ResponseCache
{
    private readonly object _lock = new();
    private readonly Dictionary<string, string> _exact = new(StringComparer.Ordinal);
    private readonly List<(double[] Vector, string Answer)> _semantic = new();

    // ------------------------------------------------------------------ exact cache

    // TODO 1.5 – Exact-match response cache (Challenge 1 "The Token Diet")
    // About a quarter of the workload repeats earlier questions. A cache hit costs $0 and takes ~0 ms.
    //   ExactKey:  the normalised question; for order-specific questions append $"|{customerId}" so that
    //              customer A never receives customer B's order answer.
    //   GetExact:  return the cached answer for the key or null (use `lock (_lock) { ... }` – items run in parallel).
    //   PutExact:  store the answer in _exact.
    // Then set "exact_cache": true in strategy.json.
    public static string ExactKey(string normalizedQuestion, string customerId, bool orderSpecific)
    {
        throw new NotImplementedException("TODO 1.5 not implemented yet – see ResponseCache.cs");
    }

    public string? GetExact(string key)
    {
        throw new NotImplementedException("TODO 1.5 not implemented yet – see ResponseCache.cs");
    }

    public void PutExact(string key, string answer)
    {
        throw new NotImplementedException("TODO 1.5 not implemented yet – see ResponseCache.cs");
    }

    // ------------------------------------------------------------------ semantic cache

    // TODO 1.6 – Semantic cache (embeddings + cosine) (Challenge 1 "The Token Diet")
    // Paraphrases ("How long do I have to return stuff?" vs "What is the return window?") miss the exact cache.
    // The pipeline embeds the question (only for NON-order questions) and passes the vector in:
    //   CosineSimilarity: dot(a, b) / (|a| * |b|)   (return 0.0 if a norm is zero)
    //   GetSemantic:      compare the vector with every cached entry in _semantic; if the BEST similarity
    //                     >= threshold return its answer, else null (read _semantic inside `lock (_lock)`).
    //   PutSemantic:      add (vector, answer) to _semantic.
    // Then set "semantic_cache": true (and tune "semantic_cache_threshold") in strategy.json.
    public static double CosineSimilarity(double[] a, double[] b)
    {
        throw new NotImplementedException("TODO 1.6 not implemented yet – see ResponseCache.cs");
    }

    public string? GetSemantic(double[] vector, double threshold)
    {
        throw new NotImplementedException("TODO 1.6 not implemented yet – see ResponseCache.cs");
    }

    public void PutSemantic(double[] vector, string answer)
    {
        throw new NotImplementedException("TODO 1.6 not implemented yet – see ResponseCache.cs");
    }
}
