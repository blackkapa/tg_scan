// Сервис A-Tracker (POST): PortfolioUpdate
// Назначение: обновить карточку актива itamPortfolio из веб-админки.
// Вход (args или __json), поддерживаются ключи в разных регистрах:
// - portfolioId | PortfolioId | assetId | AssetId | ID (обяз.)
// - lUserId | LUserId
// - categoryId | CategoryId | lCategoryId | LCategoryId
// - assetName | AssetName | sFullName | SFullName | name | Name
// - serialNo | SerialNo | sSerialNo | SSerialNo
// - inventNumber | InventNumber | sInventNumber | SInventNumber
// - comment | Comment | sComment | SComment
// - locationId | LocationId | lLocationId | LLocationId (опц.)
//
// Выход:
// data[0] = { ID, updated=true }

try
{
    string S(object o) { return o == null ? "" : o.ToString().Trim(); }
    long L(object o) { long x; return long.TryParse(S(o), out x) ? x : 0; }

    object J = null;
    if (args != null && args.ContainsKey("__json")) J = args["__json"];

    System.Collections.IDictionary d = J as System.Collections.IDictionary;
    if (d == null) d = args as System.Collections.IDictionary;
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

    long portfolioId = L(VV("portfolioId", "PortfolioId", "assetId", "AssetId", "ID", "Id"));
    long lUserId = L(VV("lUserId", "LUserId"));
    long categoryId = L(VV("categoryId", "CategoryId", "lCategoryId", "LCategoryId"));
    string assetName = S(VV("assetName", "AssetName", "sFullName", "SFullName", "name", "Name"));
    string serialNo = S(VV("serialNo", "SerialNo", "sSerialNo", "SSerialNo"));
    string inventNumber = S(VV("inventNumber", "InventNumber", "sInventNumber", "SInventNumber", "sInventoryNo", "SInventoryNo"));
    string comment = S(VV("comment", "Comment", "sComment", "SComment"));
    long locationId = L(VV("locationId", "LocationId", "lLocationId", "LLocationId", "lt_lLocationId", "Lt_lLocationId"));

    if (portfolioId <= 0)
    {
        returnCode = "Error";
        message = "portfolioId (assetId/ID) обязателен.";
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    }
    else
    {
        var p = new itamDataObject("itamPortfolio", "[ID] = " + portfolioId);
        if (p.Rows == null || p.Rows.Count == 0)
        {
            returnCode = "Error";
            message = "Карточка актива не найдена: ID=" + portfolioId;
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        }
        else
        {
            System.Exception lastEx = null;
            bool updatedOk = false;

            foreach (bool idsAsString in new[] { false, true })
            {
                try
                {
                    var p2 = new itamDataObject("itamPortfolio", "[ID] = " + portfolioId);
                    if (p2.Rows == null || p2.Rows.Count == 0)
                        throw new Exception("Карточка актива не найдена: ID=" + portfolioId);

                    var row = p2.Rows[0];
                    if (lUserId > 0) row["lUserId"] = idsAsString ? (object)S(lUserId) : (object)((int)lUserId);
                    if (!string.IsNullOrWhiteSpace(assetName))
                        row["sFullName"] = assetName;
                    if (categoryId > 0)
                    {
                        try { row["lCategoryId"] = idsAsString ? (object)S(categoryId) : (object)((int)categoryId); } catch { }
                    }
                    if (locationId > 0)
                    {
                        try { row["lLocationId"] = idsAsString ? (object)S(locationId) : (object)((int)locationId); } catch { }
                    }
                    if (!string.IsNullOrWhiteSpace(serialNo))
                        row["sSerialNo"] = serialNo;
                    if (!string.IsNullOrWhiteSpace(inventNumber))
                    {
                        if (row.ContainsKey("sInventNumber")) row["sInventNumber"] = inventNumber;
                        else row["sInventoryNo"] = inventNumber;
                    }
                    if (!string.IsNullOrWhiteSpace(comment))
                    {
                        var prev = "";
                        try
                        {
                            if (row.ContainsKey("sComment") && row["sComment"] != null)
                                prev = row["sComment"].ToString().Trim();
                        }
                        catch { }
                        if (string.IsNullOrWhiteSpace(prev))
                            row["sComment"] = comment;
                        else if (prev.IndexOf(comment, System.StringComparison.OrdinalIgnoreCase) >= 0)
                            row["sComment"] = prev;
                        else
                            row["sComment"] = prev + "\n" + comment;
                    }

                    p2.Update();
                    updatedOk = true;
                    break;
                }
                catch (System.Exception exTry)
                {
                    lastEx = exTry;
                    var msg = (exTry == null ? "" : exTry.ToString());
                    if (!idsAsString && msg.IndexOf("Int32", System.StringComparison.OrdinalIgnoreCase) >= 0
                        && msg.IndexOf("String", System.StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        continue;
                    }
                    throw;
                }
            }
            if (!updatedOk && lastEx != null) throw lastEx;
            if (!updatedOk) throw new Exception("Не удалось обновить карточку актива.");

            returnCode = "Success";
            message = "";
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>
            {
                new System.Collections.Generic.Dictionary<string, object>
                {
                    { "ID", (int)portfolioId },
                    { "updated", true }
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

