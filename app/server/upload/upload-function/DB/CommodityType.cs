using System;
using System.Collections.Generic;

namespace upload_function.DB;

public partial class CommodityType {
    public int Id { get; set; }

    public int CommodityId { get; set; }

    public string CommodityType1 { get; set; }

    public virtual Commodity Commodity { get; set; }

    public virtual ICollection<Document> Documents { get; set; } = new List<Document>();
}
