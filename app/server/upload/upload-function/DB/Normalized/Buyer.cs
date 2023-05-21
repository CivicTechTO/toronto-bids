using System;
using System.Collections.Generic;

namespace upload_function.DB.Normalized;

public partial class Buyer {
    public int Id { get; set; }

    public string Buyer1 { get; set; }

    public string Phone { get; set; }

    public string Email { get; set; }

    public int LocationId { get; set; }

    public virtual Location Location { get; set; }
}
