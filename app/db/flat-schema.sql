-- phpMyAdmin SQL Dump
-- version 4.9.7
-- https://www.phpmyadmin.net/
--
-- Host: localhost:3306
-- Generation Time: May 20, 2023 at 06:08 PM
-- Server version: 5.7.23-23
-- PHP Version: 7.3.32

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
SET AUTOCOMMIT = 0;
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `wireles1_tobids`
--

-- --------------------------------------------------------

--
-- Table structure for table `attachments`
--
--PK (CallNumber,filename)
CREATE TABLE `attachments` (
  `CallNumber` varchar(30) COLLATE utf8_unicode_ci NOT NULL, -- Reference to calls.CallNumber (TODO - MAKE THIS A FOREIGN KEY TO Calls table)
  `filename` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- Name of the file 
  `parsedtext` longtext COLLATE utf8_unicode_ci, -- (Please update)
  `lastupdated` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP -- Timestamp to track when the entry was inserted or otherwise last updated
  `uuid` varchar(36) COLLATE utf8_unicode_ci NOT NULL 
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `calls`
--

--PK (CallNumber)
CREATE TABLE `calls` (
  `CallNumber` varchar(30) COLLATE utf8_unicode_ci NOT NULL, -- Primary identifier used to differentiate individual calls/rfps/tenders
  `Commodity` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- Classification for the given rfp that defines what department of the city that the call was placed from
  `CommodityType` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- Sub classification for the given rfp that can vary based on the Commodity selected
  `Type` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- Classification for the kind of call this is. We don't have an exhaustive list of what all values this field can take (We should mention where this field is sourced from in the scraped data)
  `ShortDescription` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- Description of the rfp/tender offer/call taken from ariba
  `Description` varchar(10000) COLLATE utf8_unicode_ci NOT NULL, -- Another field taken from the description of the rfp from ariba
  `ShowDatePosted` date NOT NULL, -- The date that the call was posted
  `ClosingDate` date NOT NULL, -- The date that the call is due to be closed. This can change
  `SiteMeeting` varchar(1000) COLLATE utf8_unicode_ci NOT NULL, -- (Please update)
  `ShowBuyerNameList` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- (Rename this field to be clearer) This field is used to store the name of the buyer from the city associated with the call
  `BuyerPhoneShow` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- (Rename this field) Phone number associated with the buyer
  `BuyerEmailShow` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- (Rename this field) Email associated with the buyer for this call
  `Division` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- Another field to denote which City division the call is coming from
  `BuyerLocationShow` varchar(256) COLLATE utf8_unicode_ci NOT NULL, -- (Rename this field) This is the location of the buyer associated with this call
  `parsedtext` longtext COLLATE utf8_unicode_ci,
  `lastupdated` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP, --Timestamp field 
  `uuid` varchar(36) COLLATE utf8_unicode_ci NOT NULL -- (Please update)
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

--
-- Indexes for dumped tables
--

--
-- Indexes for table `attachments`
--
ALTER TABLE `attachments`
  ADD PRIMARY KEY (`CallNumber`,`filename`),
  ADD KEY `CallNumber` (`CallNumber`);
ALTER TABLE `attachments` ADD FULLTEXT KEY `parsedtext` (`parsedtext`);

--
-- Indexes for table `calls`
--
ALTER TABLE `calls`
  ADD PRIMARY KEY (`CallNumber`),
  ADD KEY `CallNumber` (`CallNumber`),
  ADD KEY `ShortDescription` (`ShortDescription`),
  ADD KEY `ShowBuyerNameList` (`ShowBuyerNameList`);
ALTER TABLE `calls` ADD FULLTEXT KEY `parsedtext` (`parsedtext`);
ALTER TABLE `calls` ADD FULLTEXT KEY `Description` (`Description`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
