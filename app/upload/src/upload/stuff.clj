(ns upload.stuff
	(:gen-class)
)

(defn db-spec [db-name]
	(str "jdbc:mysql:///" db-name "?user=jrootham")
)
