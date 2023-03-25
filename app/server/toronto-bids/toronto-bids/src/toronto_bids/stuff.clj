(ns toronto-bids.stuff
	(:gen-class)
	(:require [clojure.string :as str])
)

(defn db-spec [db-name]
	(str "jdbc:mysql:///" db-name "?user=jrootham")
)
