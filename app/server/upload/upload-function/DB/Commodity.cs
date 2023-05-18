using System;
using System.Collections.Generic;

namespace upload_function.DB;

public partial class Commodity {
    public int Id { get; set; }

    public string Commodity1 { get; set; }

    public virtual ICollection<CommodityType> CommodityTypes { get; set; } = new List<CommodityType>();
}
