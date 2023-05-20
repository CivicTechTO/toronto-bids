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

CREATE TABLE `attachments` (
  `CallNumber` varchar(30) COLLATE utf8_unicode_ci NOT NULL,
  `filename` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `parsedtext` longtext COLLATE utf8_unicode_ci,
  `lastupdated` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `uuid` varchar(36) COLLATE utf8_unicode_ci NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- --------------------------------------------------------

--
-- Table structure for table `calls`
--

CREATE TABLE `calls` (
  `CallNumber` varchar(30) COLLATE utf8_unicode_ci NOT NULL,
  `Commodity` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `CommodityType` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `Type` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `ShortDescription` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `Description` varchar(10000) COLLATE utf8_unicode_ci NOT NULL,
  `ShowDatePosted` date NOT NULL,
  `ClosingDate` date NOT NULL,
  `SiteMeeting` varchar(1000) COLLATE utf8_unicode_ci NOT NULL,
  `ShowBuyerNameList` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `BuyerPhoneShow` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `BuyerEmailShow` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `Division` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `BuyerLocationShow` varchar(256) COLLATE utf8_unicode_ci NOT NULL,
  `parsedtext` longtext COLLATE utf8_unicode_ci,
  `lastupdated` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `uuid` varchar(36) COLLATE utf8_unicode_ci NOT NULL
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
