using System;
using System.Collections.Generic;

namespace upload_function.DB;

public partial class Location {
    public int Id { get; set; }

    public string Location1 { get; set; }

    public virtual ICollection<Buyer> Buyers { get; set; } = new List<Buyer>();
}
