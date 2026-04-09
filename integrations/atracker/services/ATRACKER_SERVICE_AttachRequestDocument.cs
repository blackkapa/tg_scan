// Сервис A-Tracker (POST): AttachRequestDocument
// Назначение: прикрепить фото/файл к itamRequest по requestId.
// Вход JSON:
// - requestId (int, обяз.)
// - fileName (string, обяз.)
// - contentBase64 (string, обяз.)
// - contentType (string, опц.)
//
// Выход: Success/Error.

try
{
    string S(object o) { return o == null ? "" : o.ToString().Trim(); }
    int I(object o) { int x; return int.TryParse(S(o), out x) ? x : 0; }

    object J = null;
    if (args != null && args.ContainsKey("__json")) J = args["__json"];
    var d = (J as System.Collections.IDictionary) ?? (args as System.Collections.IDictionary);
    if (d == null) d = new System.Collections.Hashtable();
    object V(string key)
    {
        if (d.Contains(key)) return d[key];
        foreach (System.Collections.DictionaryEntry de in d)
        {
            if (de.Key != null && string.Equals(de.Key.ToString(), key, System.StringComparison.OrdinalIgnoreCase))
                return de.Value;
        }
        return null;
    }

    System.Collections.Generic.Dictionary<string, object> jdict =
        new System.Collections.Generic.Dictionary<string, object>(System.StringComparer.OrdinalIgnoreCase);
    try
    {
        string raw = S(J);
        if (string.IsNullOrWhiteSpace(raw) && args != null && args.ContainsKey("body"))
            raw = S(args["body"]);
        if (!string.IsNullOrWhiteSpace(raw) && raw.TrimStart().StartsWith("{"))
        {
            var doc = System.Text.Json.JsonDocument.Parse(raw);
            foreach (var p in doc.RootElement.EnumerateObject())
            {
                if (p.Value.ValueKind == System.Text.Json.JsonValueKind.String) jdict[p.Name] = p.Value.GetString();
                else if (p.Value.ValueKind == System.Text.Json.JsonValueKind.Number)
                {
                    long n;
                    if (p.Value.TryGetInt64(out n)) jdict[p.Name] = n;
                    else jdict[p.Name] = p.Value.ToString();
                }
                else if (p.Value.ValueKind == System.Text.Json.JsonValueKind.True || p.Value.ValueKind == System.Text.Json.JsonValueKind.False)
                    jdict[p.Name] = p.Value.GetBoolean();
                else
                    jdict[p.Name] = p.Value.ToString();
            }
        }
    }
    catch { }

    object VV(params string[] keys)
    {
        foreach (var k in keys)
        {
            var v = V(k);
            if (v != null && !string.IsNullOrWhiteSpace(S(v))) return v;
            if (jdict.ContainsKey(k) && jdict[k] != null && !string.IsNullOrWhiteSpace(S(jdict[k]))) return jdict[k];
        }
        return null;
    }

    int requestId = I(VV("requestId", "RequestId", "lRequestId", "LRequestId"));
    string fileName = S(VV("fileName", "FileName", "name", "Name"));
    string contentBase64 = S(VV("contentBase64", "ContentBase64", "fileBase64", "FileBase64", "content", "Content"));
    string contentType = S(VV("contentType", "ContentType", "mimeType", "MimeType"));
    if (string.IsNullOrWhiteSpace(contentType)) contentType = "application/octet-stream";

    if (requestId <= 0 || string.IsNullOrWhiteSpace(fileName) || string.IsNullOrWhiteSpace(contentBase64))
    {
        returnCode = "Error";
        message = "requestId, fileName и contentBase64 обязательны.";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else
    {
        var req = new itamDataObject("itamRequest", "[ID] = " + requestId);
        if (req.Rows == null || req.Rows.Count == 0)
        {
            returnCode = "Error";
            message = "Заявка itamRequest не найдена: ID=" + requestId;
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        }
        else
        {
            byte[] bytes = System.Convert.FromBase64String(contentBase64);
            // Параметры 3,7 могут отличаться в вашей инсталляции. Оставлено по аналогии с существующим сервисом Asset_AddFile.
            new HelperMethods().SaveFile(fileName, bytes, "itamRequest", requestId, 3, 7);

            returnCode = "Success";
            message = "";
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>
            {
                new System.Collections.Generic.Dictionary<string, object>
                {
                    { "requestId", requestId },
                    { "fileName", fileName },
                    { "contentType", contentType }
                }
            };
        }
    }
}
catch (System.Exception ex)
{
    returnCode = "Error";
    message = ex.ToString();
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
}
