// Сервис A-Tracker: список местоположений (GET) для формы «Передача техники».
// Скопируйте в карточку сервиса (как ReturnEmpl / Asset_User). В config.ini: locations_list_service_id = <ID>.
//
// Если при сохранении ошибка «неизвестное поле» — поправьте имя поля родителя в вашей схеме (часто lParentId; реже lParentLocationId).

try
{
    var fields = new System.Collections.Generic.List<string> { "ID", "sFullName", "lParentId" };
    var loc = new itamDataObject(
        "itamLocation",
        fields: fields,
        where: "1=1",
        parameters: new System.Collections.Generic.Dictionary<string, object>());

    var list = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
    if (loc.Rows != null)
    {
        foreach (var row in loc.Rows)
        {
            var d = new System.Collections.Generic.Dictionary<string, object>();
            if (row.ContainsKey("ID"))
                d["ID"] = row["ID"];
            string name = "";
            if (row.ContainsKey("sFullName") && row["sFullName"] != null)
                name = row["sFullName"].ToString();
            else if (row.ContainsKey("SFullName") && row["SFullName"] != null)
                name = row["SFullName"].ToString();
            d["sFullName"] = name;
            if (row.ContainsKey("lParentId") && row["lParentId"] != null)
                d["lParentId"] = row["lParentId"];
            list.Add(d);
        }
    }

    data = list;
    returnCode = "Success";
    message = "OK";
}
catch (System.Exception ex)
{
    returnCode = "Error";
    message = ex.ToString();
    data = new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string, object>>();
}
