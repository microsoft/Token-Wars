namespace TokenWars;

/// <summary>In-memory response caches: exact match (TODO 1.5) and semantic (TODO 1.6). Thread-safe.</summary>
public sealed class ResponseCache
{
    private readonly object _lock = new();
    private readonly Dictionary<string, string> _exact = new(StringComparer.Ordinal);
    private readonly List<(double[] Vector, string Answer)> _semantic = new();

    // ------------------------------------------------------------------ exact cache

    // SOLUTION 1.5 – exact-match cache keyed by the normalised question (+ customer for order questions).
    public static string ExactKey(string normalizedQuestion, string customerId, bool orderSpecific) =>
        orderSpecific ? $"{normalizedQuestion}|{customerId}" : normalizedQuestion;

    public string? GetExact(string key)
    {
        lock (_lock)
        {
            return _exact.TryGetValue(key, out var answer) ? answer : null;
        }
    }

    public void PutExact(string key, string answer)
    {
        lock (_lock)
        {
            _exact[key] = answer;
        }
    }

    // ------------------------------------------------------------------ semantic cache

    // SOLUTION 1.6 – semantic cache: cosine similarity against cached (non-order) question embeddings.
    public static double CosineSimilarity(double[] a, double[] b)
    {
        int n = Math.Min(a.Length, b.Length);
        double dot = 0;
        for (int i = 0; i < n; i++) dot += a[i] * b[i];
        double normA = Math.Sqrt(a.Sum(x => x * x));
        double normB = Math.Sqrt(b.Sum(y => y * y));
        return normA > 0 && normB > 0 ? dot / (normA * normB) : 0.0;
    }

    public string? GetSemantic(double[] vector, double threshold)
    {
        List<(double[] Vector, string Answer)> entries;
        lock (_lock)
        {
            entries = new List<(double[] Vector, string Answer)>(_semantic);
        }
        double bestScore = -1.0;
        string? bestAnswer = null;
        foreach (var entry in entries)
        {
            double score = CosineSimilarity(vector, entry.Vector);
            if (score > bestScore)
            {
                bestScore = score;
                bestAnswer = entry.Answer;
            }
        }
        return bestAnswer != null && bestScore >= threshold ? bestAnswer : null;
    }

    public void PutSemantic(double[] vector, string answer)
    {
        lock (_lock)
        {
            _semantic.Add((vector, answer));
        }
    }
}
