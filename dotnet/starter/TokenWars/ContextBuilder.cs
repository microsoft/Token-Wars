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
    private readonly string? _embeddingsFile; // for the embedding-retrieval stretch goal

    public ContextBuilder(string root, Strategy strategy, LlmClient client, string? embeddingsFile = null)
    {
        _strategy = strategy;
        _client = client;
        _embeddingsFile = embeddingsFile;
        Docs = LoadKnowledgeBase(root);
        Sections = SplitSections(Docs);
        Orders = LoadOrders(root);
        FullKb = string.Join("\n\n", Docs.Select(d => d.Content));

        // The unique search terms of every section (same order as Sections) – handy for TODO 1.2.
        SectionTerms = Sections.Select(s => new HashSet<string>(TextUtil.Tokenize(s.Text), StringComparer.Ordinal)).ToList();
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

    // TODO 1.2 – Keyword retrieval of the top-k KB sections (Challenge 1 "The Token Diet")
    // retrieval="all" sends the WHOLE knowledge base (thousands of tokens) with every question. Send only the
    // relevant sections instead:
    //   1. queryTerms = the distinct TextUtil.Tokenize(question) tokens
    //   2. N = Sections.Count; df(term) = number of sections whose SectionTerms contain the term;
    //      idf(term) = Math.Log(1 + (double)N / df(term))          (tip: compute the idf table once and keep it)
    //   3. score(section) = sum of idf over the query terms present in that section
    //   4. take the topK highest scores (ties keep the original section order – OrderByDescending is stable)
    //   5. return the section texts (Sections[i].Text) joined with SectionSeparator
    // Then set "retrieval": "keyword" in strategy.json.
    public string KeywordRetrieval(string question, int topK)
    {
        throw new NotImplementedException("TODO 1.2 not implemented yet – see ContextBuilder.cs");
    }

    // STRETCH GOAL – embedding retrieval (retrieval="embedding"; optional)
    // Embed all section texts once with _client.EmbedAsync("embedding", texts, ct) (batch them and add each
    // result.Call to `calls` so the one-off cost is counted), optionally cache the vectors in _embeddingsFile keyed by
    // the embedding deployment, then return the topK sections by cosine similarity with `await questionVector()`.
    public Task<string> EmbeddingRetrievalAsync(
        Func<Task<double[]>> questionVector, int topK, List<CallRecord> calls, CancellationToken ct = default)
    {
        throw new NotImplementedException("Stretch goal (embedding retrieval) not implemented yet – see ContextBuilder.cs");
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

    // TODO 1.3 – Order lookup: only the relevant orders (Challenge 1 "The Token Diet")
    // order_lookup=false pastes the WHOLE orders database into every prompt – even for "Do you ship to Austria?".
    // Return only what the question needs:
    //   - if the question contains order ids (TextUtil.OrderIdRegex, e.g. "BC-10042") -> just the matching order(s)
    //     (compare upper-case ids; OrderField(order, "order_id") reads a field)
    //   - else if orderSpecific -> this customer's 3 most recent orders (sort by "placed_at", newest first)
    //   - else -> an empty list (no orders at all)
    // Then set "order_lookup": true in strategy.json.
    public List<JsonNode> LookupOrders(string question, string customerId, bool orderSpecific)
    {
        throw new NotImplementedException("TODO 1.3 not implemented yet – see ContextBuilder.cs");
    }

    /// <summary>A string field of an order object (null if missing).</summary>
    public static string? OrderField(JsonNode order, string name) =>
        order is JsonObject o ? JsonUtil.Str(o[name]) : null;
}
