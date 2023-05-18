using System;
using System.Collections.Generic;
using Azure.Identity;
using Azure.Security.KeyVault.Secrets;
using Microsoft.EntityFrameworkCore;

namespace upload_function.DB;

public partial class BidsDBContext : DbContext {
    public BidsDBContext() {
    }

    public BidsDBContext(DbContextOptions<BidsDBContext> options)
        : base(options) {
    }

    public virtual DbSet<Attachment> Attachments { get; set; }

    public virtual DbSet<Buyer> Buyers { get; set; }

    public virtual DbSet<Commodity> Commodities { get; set; }

    public virtual DbSet<CommodityType> CommodityTypes { get; set; }

    public virtual DbSet<Division> Divisions { get; set; }

    public virtual DbSet<Document> Documents { get; set; }

    public virtual DbSet<DocumentBuyer> DocumentBuyers { get; set; }

    public virtual DbSet<Header> Headers { get; set; }

    public virtual DbSet<Location> Locations { get; set; }

    public virtual DbSet<OfferType> Types { get; set; }

    protected override void OnConfiguring(DbContextOptionsBuilder optionsBuilder) {
        if (!optionsBuilder.IsConfigured)
        {
            const string secretName = "DBCONNECTIONSTRINGUPLOADER";
            var keyVaultName = "obt-keys";
            var kvUri = $"https://{keyVaultName}.vault.azure.net";

            var client = new SecretClient(new Uri(kvUri), new DefaultAzureCredential());

            var secret = client.GetSecret(secretName);
            optionsBuilder.UseMySql(secret.Value.Value, ServerVersion.Parse("5.7.42-mysql"));
        }
    }

    protected override void OnModelCreating(ModelBuilder modelBuilder) {
        modelBuilder
            .UseCollation("utf8_general_ci")
            .HasCharSet("utf8");

        modelBuilder.Entity<Attachment>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("attachment");

            entity.HasIndex(e => e.DocumentId, "document_id");

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.FileName)
                  .IsRequired()
                  .HasColumnName("file_name");
            entity.Property(e => e.DatastoreFileURL)
                  .IsRequired()
                  .HasColumnName("file_link");
            entity.Property(e => e.AttachmentPath)
                  .HasColumnName("file_location");
            entity.Property(e => e.DocumentId)
                .HasColumnType("int(11)")
                .HasColumnName("document_id");

            entity.HasOne(d => d.Document).WithMany(p => p.Attachments)
                .HasForeignKey(d => d.DocumentId)
                .OnDelete(DeleteBehavior.ClientSetNull)
                .HasConstraintName("attachment_ibfk_1");
        });

        modelBuilder.Entity<Buyer>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("buyer");

            entity.HasIndex(e => e.LocationId, "location_id");

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.Buyer1)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("buyer");
            entity.Property(e => e.Email)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("email");
            entity.Property(e => e.LocationId)
                .HasColumnType("int(11)")
                .HasColumnName("location_id");
            entity.Property(e => e.Phone)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("phone");

            entity.HasOne(d => d.Location).WithMany(p => p.Buyers)
                .HasForeignKey(d => d.LocationId)
                .OnDelete(DeleteBehavior.ClientSetNull)
                .HasConstraintName("buyer_ibfk_1");
        });

        modelBuilder.Entity<Commodity>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("commodity");

            entity.HasIndex(e => e.Commodity1, "commodity").IsUnique();

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.Commodity1)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("commodity");
        });

        modelBuilder.Entity<CommodityType>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("commodity_type");

            entity.HasIndex(e => new { e.CommodityId, e.CommodityType1 }, "commodity_id").IsUnique();

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.CommodityId)
                .HasColumnType("int(11)")
                .HasColumnName("commodity_id");
            entity.Property(e => e.CommodityType1)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("commodity_type");

            entity.HasOne(d => d.Commodity).WithMany(p => p.CommodityTypes)
                .HasForeignKey(d => d.CommodityId)
                .OnDelete(DeleteBehavior.ClientSetNull)
                .HasConstraintName("commodity_type_ibfk_1");
        });

        modelBuilder.Entity<Division>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("division");

            entity.HasIndex(e => e.Division1, "division").IsUnique();

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.Division1)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("division");
        });

        modelBuilder.Entity<Document>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("document");

            entity.HasIndex(e => e.CommodityTypeId, "commodity_type_id");

            entity.HasIndex(e => e.DivisionId, "division_id");

            entity.HasIndex(e => new { e.ShortDescription, e.Description, e.SearchText }, "short_description").HasAnnotation("MySql:FullTextIndex", true);

            entity.HasIndex(e => e.TypeId, "type_id");

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.CallNumber)
                .IsRequired()
                .HasMaxLength(30)
                .HasColumnName("call_number");
            entity.Property(e => e.ClosingDate).HasColumnName("closing_date");
            entity.Property(e => e.CommodityTypeId)
                .HasColumnType("int(11)")
                .HasColumnName("commodity_type_id");
            entity.Property(e => e.Description)
                .IsRequired()
                .HasColumnType("text")
                .HasColumnName("description");
            entity.Property(e => e.DivisionId)
                .HasColumnType("int(11)")
                .HasColumnName("division_id");
            entity.Property(e => e.LastUpdated)
                .HasDefaultValueSql("CURRENT_TIMESTAMP")
                .HasColumnType("timestamp")
                .HasColumnName("last_updated");
            entity.Property(e => e.PostingDate).HasColumnName("posting_date");
            entity.Property(e => e.SearchText)
                .IsRequired()
                .HasColumnType("longtext")
                .HasColumnName("search_text");
            entity.Property(e => e.ShortDescription)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("short_description");
            entity.Property(e => e.SiteMeeting)
                .IsRequired()
                .HasMaxLength(1000)
                .HasColumnName("site_meeting");
            entity.Property(e => e.TypeId)
                .HasColumnType("int(11)")
                .HasColumnName("type_id");

            entity.HasOne(d => d.CommodityType).WithMany(p => p.Documents)
                .HasForeignKey(d => d.CommodityTypeId)
                .OnDelete(DeleteBehavior.ClientSetNull)
                .HasConstraintName("document_ibfk_2");

            entity.HasOne(d => d.Division).WithMany(p => p.Documents)
                .HasForeignKey(d => d.DivisionId)
                .OnDelete(DeleteBehavior.ClientSetNull)
                .HasConstraintName("document_ibfk_3");

            entity.HasOne(d => d.OfferType).WithMany(p => p.Documents)
                .HasForeignKey(d => d.TypeId)
                .OnDelete(DeleteBehavior.ClientSetNull)
                .HasConstraintName("document_ibfk_1");
        });

        modelBuilder.Entity<DocumentBuyer>(entity =>
        {
            entity.HasKey(e => new { e.DocumentId, e.BuyerId })
                .HasName("PRIMARY")
                .HasAnnotation("MySql:IndexPrefixLength", new[] { 0, 0 });

            entity.ToTable("document_buyer");

            entity.Property(e => e.DocumentId)
                .HasColumnType("int(11)")
                .HasColumnName("document_id");
            entity.Property(e => e.BuyerId)
                .HasColumnType("int(11)")
                .HasColumnName("buyer_id");
        });

        modelBuilder.Entity<Header>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("header");

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.AttachmentId)
                .HasColumnType("int(11)")
                .HasColumnName("attachment_id");
            entity.Property(e => e.Header1)
                .IsRequired()
                .HasMaxLength(1000)
                .HasColumnName("header");
        });

        modelBuilder.Entity<Location>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("location");

            entity.HasIndex(e => e.Location1, "location").IsUnique();

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.Location1)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("location");
        });

        modelBuilder.Entity<OfferType>(entity =>
        {
            entity.HasKey(e => e.Id).HasName("PRIMARY");

            entity.ToTable("type");

            entity.HasIndex(e => e.Type1, "type").IsUnique();

            entity.Property(e => e.Id)
                .HasColumnType("int(11)")
                .HasColumnName("id");
            entity.Property(e => e.Type1)
                .IsRequired()
                .HasMaxLength(256)
                .HasColumnName("type");
        });

        OnModelCreatingPartial(modelBuilder);
    }

    partial void OnModelCreatingPartial(ModelBuilder modelBuilder);
}
