using System;
using System.Collections.Generic;

namespace upload_function.DB.Normalized;

public partial class CommoditySubType {
    public int Id { get; set; }

    public int CommodityId { get; set; }

    public string SubTypeName { get; set; }

    public virtual Commodity Commodity { get; set; }

    public virtual ICollection<Document> Documents { get; set; } = new List<Document>();
}
