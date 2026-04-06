// Сервис A-Tracker ID4 (asset_find): карточка актива по AssetId для веба и бота.
// GET /Api/Service?id=<asset_info_service_id>&AssetId=<id>
//
// В ответ кладём строку itamPortfolio (все запрошенные поля) + OwnerFio — так Python (atracker_client)
// может разобрать lt_lCategoryId / lt_lLocationId и показать «Категорию» и местоположение.
// Поля с точками — развёртка FK в модели A-Tracker (см. карточку объекта itamPortfolio в админке).

try
{
    var assetIdParam = args.ContainsKey("AssetId") ? args["AssetId"] : (args.ContainsKey("assetid") ? args["assetid"] : null);
    if (assetIdParam == null || string.IsNullOrWhiteSpace(assetIdParam.ToString()))
    {
        data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
        returnCode = "Error";
        message = "Не передан параметр AssetId";
    }
    else
    {
        int assetId = System.Convert.ToInt32(assetIdParam);

        var fields = new System.Collections.Generic.List<string>()
        {
            "ID",
            "sFullName",
            "sSerialNo",
            "sInventNumber",
            "sInventoryNo",
            "sInventNo",
            "iQty",
            "lUserId",
            "lUserId.sFullName",
            "lt_lLocationId",
            "lt_lLocationId.ID",
            "lt_lLocationId.sFullName",
            "lt_lCategoryId",
            "lt_lCategoryId.ID",
            "lt_lCategoryId.sFullName",
            "lModelId",
            "lModelId.sFullName",
            "lModelId.lCategoryId",
            "lModelId.lCategoryId.sFullName",
            "lModelId.lCategoryId.sName",
        };

        var asset = new itamDataObject(
            "itamPortfolio",
            fields: fields,
            where: "[ID] = @P1",
            parameters: new System.Collections.Generic.Dictionary<string, object>()
            {
                { "@P1", assetId }
            }
        );

        if (asset.Rows == null || asset.Rows.Count == 0)
        {
            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
            returnCode = "Success";
            message = "";
        }
        else
        {
            var row = asset.Rows[0];

            var ownerFio = "—";
            if (row.ContainsKey("lUserId") && row["lUserId"] != null)
            {
                var lUserIdVal = row["lUserId"];
                if (lUserIdVal is System.Collections.Generic.Dictionary<string, object> dict && dict.ContainsKey("sFullName") && dict["sFullName"] != null)
                    ownerFio = dict["sFullName"].ToString().Trim();
                else if (lUserIdVal is System.Collections.IDictionary idict && idict.Contains("sFullName") && idict["sFullName"] != null)
                    ownerFio = idict["sFullName"].ToString().Trim();
                else if (!(lUserIdVal is int || lUserIdVal is long) && !string.IsNullOrWhiteSpace(lUserIdVal.ToString()))
                    ownerFio = lUserIdVal.ToString().Trim();
            }
            if (ownerFio == "—")
            {
                var ownerKeys = new[] { "lUserId.sFullName", "lUserId_sFullName", "lUser.sFullName", "lUser_sFullName", "User", "sUser", "OwnerFio", "Fio", "Owner" };
                foreach (var key in ownerKeys)
                {
                    if (row.ContainsKey(key) && row[key] != null && !string.IsNullOrWhiteSpace(row[key].ToString()))
                    {
                        ownerFio = row[key].ToString().Trim();
                        break;
                    }
                }
            }
            if (ownerFio == "—" && row.ContainsKey("lUserId") && row["lUserId"] != null)
            {
                var uid = row["lUserId"];
                int userId = 0;
                if (uid is int i) userId = i;
                else if (uid is long l) userId = (int)l;
                else int.TryParse(uid.ToString(), out userId);
                if (userId != 0)
                {
                    var tableNames = new[] { "ItamEmplDept", "itamEmplDept", "itamUser" };
                    foreach (var tableName in tableNames)
                    {
                        try
                        {
                            var userObj = new itamDataObject(tableName, fields: new System.Collections.Generic.List<string>() { "sFullName" }, where: "[ID] = @P1", parameters: new System.Collections.Generic.Dictionary<string, object>() { { "@P1", userId } });
                            if (userObj.Rows != null && userObj.Rows.Count > 0)
                            {
                                var urow = userObj.Rows[0];
                                var name = (urow.ContainsKey("sFullName") && urow["sFullName"] != null) ? urow["sFullName"].ToString().Trim() : null;
                                if (!string.IsNullOrWhiteSpace(name))
                                {
                                    ownerFio = name;
                                    break;
                                }
                            }
                        }
                        catch { }
                    }
                }
            }

            var result = new System.Collections.Generic.Dictionary<string, object>();
            foreach (var kv in row)
                result[kv.Key] = kv.Value;

            result["OwnerFio"] = ownerFio;

            data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>> { result };
            returnCode = "Success";
            message = "";
        }
    }
}
catch (System.Exception ex)
{
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    returnCode = "Error";
    message = ex.ToString();
}
