using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace TokenWars;

/// <summary>Text helpers shared by retrieval, caching, routing and the mock embeddings.</summary>
public static class TextUtil
{
    public static readonly Regex OrderIdRegex = new(@"BC-[0-9]{5}", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    /// <summary>Words that make a question "order-specific" (SPEC 4.2 step 2).</summary>
    public static readonly string[] OrderMarkers =
    {
        "order", "package", "parcel", "delivery", "delivered", "tracking", "shipped", "refund status", "my purchase",
    };

    private static readonly Regex TokenRegex = new("[a-z0-9]+", RegexOptions.CultureInvariant);
    private static readonly Regex WhitespaceRegex = new(@"\s+", RegexOptions.CultureInvariant);

    /// <summary>Fixed English stopword list (identical to the Python app).</summary>
    public static readonly HashSet<string> Stopwords = new(StringComparer.Ordinal)
    {
        "a", "about", "after", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be", "been", "before",
        "but", "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if",
        "in", "into", "is", "it", "its", "just", "me", "my", "no", "not", "of", "on", "or", "our", "so", "than",
        "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "was", "we", "what",
        "when", "where", "which", "who", "why", "will", "with", "would", "you", "your",
    };

    /// <summary>Lowercase [a-z0-9]+ tokens, without stopwords and tokens shorter than 3 chars (SPEC 4.5).</summary>
    public static List<string> Tokenize(string text)
    {
        var tokens = new List<string>();
        foreach (Match m in TokenRegex.Matches(text.ToLowerInvariant()))
        {
            var t = m.Value;
            if (t.Length >= 3 && !Stopwords.Contains(t)) tokens.Add(t);
        }
        return tokens;
    }

    /// <summary>Lowercase, trim, collapse whitespace, strip trailing punctuation.</summary>
    public static string Normalize(string question)
    {
        var text = question.Replace('\u2019', '\'').Trim().ToLowerInvariant();
        text = WhitespaceRegex.Replace(text, " ");
        return text.TrimEnd(' ', '.', ',', '!', '?', ';', ':', '\u2026').Trim();
    }

    public static bool IsOrderSpecific(string question)
    {
        var normalized = Normalize(question);
        return OrderIdRegex.IsMatch(normalized)
            || OrderMarkers.Any(marker => normalized.Contains(marker, StringComparison.Ordinal));
    }

    /// <summary>FNV-1a 32-bit hash of the UTF-8 bytes (stable across languages).</summary>
    public static uint Fnv1a32(string text)
    {
        uint hash = 2166136261;
        foreach (byte b in Encoding.UTF8.GetBytes(text))
        {
            hash ^= b;
            hash = unchecked(hash * 16777619);
        }
        return hash;
    }

    /// <summary>Mock embedding: 256-dim bag-of-words hashing vector, L2-normalised (SPEC 4.6).</summary>
    public static double[] HashEmbedding(string text)
    {
        var vector = new double[256];
        foreach (var token in Tokenize(text))
        {
            vector[(int)(Fnv1a32(token) % 256)] += 1.0;
        }
        double norm = Math.Sqrt(vector.Sum(v => v * v));
        if (norm > 0)
        {
            for (int i = 0; i < vector.Length; i++) vector[i] /= norm;
        }
        return vector;
    }

    /// <summary>Number of Unicode code points (same as Python's len()).</summary>
    public static int CodePointCount(string text)
    {
        int count = 0;
        for (int i = 0; i < text.Length; i++)
        {
            if (char.IsHighSurrogate(text[i]) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1])) i++;
            count++;
        }
        return count;
    }

    /// <summary>The first n code points of text (same as Python's text[:n]).</summary>
    public static string TakeCodePoints(string text, int n)
    {
        int count = 0;
        int i = 0;
        while (i < text.Length && count < n)
        {
            if (char.IsHighSurrogate(text[i]) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1])) i += 2;
            else i++;
            count++;
        }
        return text.Substring(0, i);
    }
}

public sealed record KbDoc(string File, string Title, string Content);

/// <summary>One "## " section: Text = "&lt;Doc Title&gt; &gt; &lt;Section Title&gt;\n&lt;body&gt;".</summary>
public sealed record KbSection(string DocTitle, string Title, string Text);

/// <summary>Builds the knowledge-base and order context for a question (SPEC 4.5).</summary>
public sealed class ContextBuilder
{
    public const string SectionSeparator = "\n\n---\n\n";

    private readonly Strategy _strategy;
    private readonly LlmClient _client;
    private readonly string? _embeddingsFile;
    private readonly Dictionary<string, double> _idf = new(StringComparer.Ordinal);
    private readonly SemaphoreSlim _indexLock = new(1, 1);
    private List<double[]>? _sectionVectors;

    public ContextBuilder(string root, Strategy strategy, LlmClient client, string? embeddingsFile = null)
    {
        _strategy = strategy;
        _client = client;
        _embeddingsFile = embeddingsFile;
        Docs = LoadKnowledgeBase(root);
        Sections = SplitSections(Docs);
        Orders = LoadOrders(root);
        FullKb = string.Join("\n\n", Docs.Select(d => d.Content));

        // The unique search terms of every section (same order as Sections).
        SectionTerms = Sections.Select(s => new HashSet<string>(TextUtil.Tokenize(s.Text), StringComparer.Ordinal)).ToList();
        BuildIdf();
    }

    public List<KbDoc> Docs { get; }
    public List<KbSection> Sections { get; }
    public List<HashSet<string>> SectionTerms { get; }
    public List<JsonNode> Orders { get; }
    public string FullKb { get; }

    // ------------------------------------------------------------------ loading

    public static List<KbDoc> LoadKnowledgeBase(string root)
    {
        var kbDir = Path.Combine(root, "shared", "knowledge-base");
        var docs = new List<KbDoc>();
        if (!Directory.Exists(kbDir)) return docs;
        foreach (var path in Directory.GetFiles(kbDir, "*.md").OrderBy(p => Path.GetFileName(p), StringComparer.Ordinal))
        {
            var content = File.ReadAllText(path).Replace("\r\n", "\n").Trim();
            var title = Path.GetFileNameWithoutExtension(path);
            foreach (var line in content.Split('\n'))
            {
                if (line.StartsWith("# ", StringComparison.Ordinal))
                {
                    title = line.Substring(2).Trim();
                    break;
                }
            }
            docs.Add(new KbDoc(Path.GetFileName(path), title, content));
        }
        return docs;
    }

    public static List<KbSection> SplitSections(List<KbDoc> docs)
    {
        var sections = new List<KbSection>();
        foreach (var doc in docs)
        {
            string? currentTitle = null;
            var body = new List<string>();
            foreach (var line in doc.Content.Split('\n'))
            {
                if (line.StartsWith("## ", StringComparison.Ordinal))
                {
                    if (currentTitle != null) sections.Add(MakeSection(doc.Title, currentTitle, body));
                    currentTitle = line.Substring(3).Trim();
                    body = new List<string>();
                }
                else if (currentTitle != null)
                {
                    body.Add(line);
                }
            }
            if (currentTitle != null) sections.Add(MakeSection(doc.Title, currentTitle, body));
        }
        return sections;
    }

    private static KbSection MakeSection(string docTitle, string title, List<string> body) =>
        new(docTitle, title, $"{docTitle} > {title}\n" + string.Join("\n", body).Trim());

    public static List<JsonNode> LoadOrders(string root)
    {
        var path = Path.Combine(root, "shared", "data", "orders.json");
        var orders = new List<JsonNode>();
        if (!File.Exists(path)) return orders;
        if (JsonUtil.ParseFile(path) is JsonArray array)
        {
            foreach (var order in array)
            {
                if (order != null) orders.Add(order);
            }
        }
        return orders;
    }

    /// <summary>Compact JSON array (no spaces, non-ASCII kept) – like Python's json.dumps(separators=(",", ":")).</summary>
    public static string OrdersToJson(List<JsonNode> orders) =>
        "[" + string.Join(",", orders.Select(o => o.ToJsonString(JsonUtil.Compact))) + "]";

    // ------------------------------------------------------------------ knowledge base

    public async Task<string> KbContextAsync(
        string question, List<CallRecord> calls, Func<Task<double[]>> questionVector, CancellationToken ct = default)
    {
        switch (_strategy.Retrieval)
        {
            case "all":
                return FullKb;
            case "keyword":
                return KeywordRetrieval(question, _strategy.TopK);
            case "embedding":
                return await EmbeddingRetrievalAsync(questionVector, _strategy.TopK, calls, ct);
            default:
                throw new InvalidOperationException($"Unknown retrieval mode {_strategy.Retrieval}");
        }
    }

    private void BuildIdf()
    {
        int n = Sections.Count;
        var df = new Dictionary<string, int>(StringComparer.Ordinal);
        foreach (var terms in SectionTerms)
        {
            foreach (var term in terms)
            {
                df[term] = df.TryGetValue(term, out var count) ? count + 1 : 1;
            }
        }
        foreach (var (term, count) in df)
        {
            _idf[term] = Math.Log(1.0 + (double)n / count);
        }
    }

    // SOLUTION 1.2 – keyword retrieval: score = Σ idf of the unique query terms present in the section,
    // idf = ln(1 + N / df); top_k highest (ties keep the original section order).
    public string KeywordRetrieval(string question, int topK)
    {
        var queryTerms = TextUtil.Tokenize(question).Distinct().ToList();
        var scored = new List<(double Score, int Index)>();
        for (int i = 0; i < SectionTerms.Count; i++)
        {
            double score = 0;
            foreach (var term in queryTerms)
            {
                if (SectionTerms[i].Contains(term)) score += _idf[term];
            }
            scored.Add((score, i));
        }
        // OrderByDescending is a stable sort -> ties keep the original order.
        var top = scored.OrderByDescending(x => x.Score).Take(topK).Select(x => Sections[x.Index].Text);
        return string.Join(SectionSeparator, top);
    }

    // SOLUTION (stretch) – embedding retrieval: embed all sections once, then cosine top-k.
    public async Task<string> EmbeddingRetrievalAsync(
        Func<Task<double[]>> questionVector, int topK, List<CallRecord> calls, CancellationToken ct = default)
    {
        var vectors = await EnsureSectionVectorsAsync(calls, ct);
        var query = await questionVector();
        var scored = vectors.Select((v, i) => (Score: ResponseCache.CosineSimilarity(query, v), Index: i)).ToList();
        var top = scored.OrderByDescending(x => x.Score).Take(topK).Select(x => Sections[x.Index].Text);
        return string.Join(SectionSeparator, top);
    }

    private async Task<List<double[]>> EnsureSectionVectorsAsync(List<CallRecord> calls, CancellationToken ct)
    {
        await _indexLock.WaitAsync(ct);
        try
        {
            if (_sectionVectors != null) return _sectionVectors;

            var deployment = _client.Model("embedding").Deployment;
            var cacheKey = (_client.Mock ? "mock:" : "") + deployment;
            var fingerprint = Convert.ToHexString(
                    SHA256.HashData(Encoding.UTF8.GetBytes(string.Join("\n\u0000", Sections.Select(s => s.Text)))))
                .ToLowerInvariant();

            var stored = ReadEmbeddingsFile();
            if (stored[cacheKey] is JsonObject entry
                && JsonUtil.Str(entry["fingerprint"]) == fingerprint
                && entry["vectors"] is JsonArray cachedVectors
                && cachedVectors.Count == Sections.Count)
            {
                _sectionVectors = cachedVectors
                    .Select(v => v is JsonArray a ? a.Select(x => JsonUtil.Dbl(x) ?? 0).ToArray() : Array.Empty<double>())
                    .ToList();
                return _sectionVectors;
            }

            var vectors = new List<double[]>();
            var texts = Sections.Select(s => s.Text).ToList();
            for (int start = 0; start < texts.Count; start += 64)
            {
                var result = await _client.EmbedAsync("embedding", texts.Skip(start).Take(64).ToList(), ct);
                calls.Add(result.Call); // cost is counted once, on the item that triggers the indexing
                vectors.AddRange(result.Vectors);
            }
            _sectionVectors = vectors;

            if (_embeddingsFile != null)
            {
                var vectorArray = new JsonArray();
                foreach (var v in vectors)
                {
                    var inner = new JsonArray();
                    foreach (var x in v) inner.Add((JsonNode)x);
                    vectorArray.Add((JsonNode)inner);
                }
                stored[cacheKey] = new JsonObject { ["fingerprint"] = fingerprint, ["vectors"] = vectorArray };
                try
                {
                    var dir = Path.GetDirectoryName(_embeddingsFile);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    File.WriteAllText(_embeddingsFile, stored.ToJsonString(JsonUtil.Compact));
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                    Console.Error.WriteLine($"⚠️  Could not write {_embeddingsFile}: {ex.Message}");
                }
            }
            return vectors;
        }
        finally
        {
            _indexLock.Release();
        }
    }

    private JsonObject ReadEmbeddingsFile()
    {
        if (_embeddingsFile == null || !File.Exists(_embeddingsFile)) return new JsonObject();
        try
        {
            return JsonNode.Parse(File.ReadAllText(_embeddingsFile)) as JsonObject ?? new JsonObject();
        }
        catch (Exception)
        {
            return new JsonObject();
        }
    }

    // ------------------------------------------------------------------ orders

    public List<JsonNode> OrdersContext(string question, string customerId, bool orderSpecific)
    {
        if (!_strategy.OrderLookup)
        {
            return Orders; // baseline: the whole orders database
        }
        return LookupOrders(question, customerId, orderSpecific);
    }

    // SOLUTION 1.3 – order lookup: only the orders the question is about.
    public List<JsonNode> LookupOrders(string question, string customerId, bool orderSpecific)
    {
        var ids = new HashSet<string>(
            TextUtil.OrderIdRegex.Matches(question).Select(m => m.Value.ToUpperInvariant()),
            StringComparer.Ordinal);
        if (ids.Count > 0)
        {
            return Orders.Where(o => ids.Contains((OrderField(o, "order_id") ?? "").ToUpperInvariant())).ToList();
        }
        if (orderSpecific)
        {
            return Orders
                .Where(o => OrderField(o, "customer_id") == customerId)
                .OrderByDescending(o => OrderField(o, "placed_at") ?? "", StringComparer.Ordinal)
                .Take(3)
                .ToList();
        }
        return new List<JsonNode>();
    }

    /// <summary>A string field of an order object (null if missing).</summary>
    public static string? OrderField(JsonNode order, string name) =>
        order is JsonObject o ? JsonUtil.Str(o[name]) : null;
}
